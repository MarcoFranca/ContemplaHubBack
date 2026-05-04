import unittest
from copy import deepcopy

from app.services.kanban_service import build_kanban_snapshot
from app.services.meta_leads_service import (
    _build_meta_ads_context,
    _build_meta_diagnostic_payload,
    _parse_meta_field_data,
    normalize_phone,
    upsert_meta_diagnostic_from_meta,
)


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTableQuery:
    def __init__(self, client: "FakeSupabaseClient", table_name: str):
        self.client = client
        self.table_name = table_name
        self._filters: list[tuple[str, str, object]] = []
        self._limit: int | None = None
        self._order_field: str | None = None
        self._order_desc = False
        self._operation = "select"
        self._payload = None

    def select(self, _columns: str):
        self._operation = "select"
        return self

    def eq(self, field: str, value: object):
        self._filters.append(("eq", field, value))
        return self

    def in_(self, field: str, values: list[object]):
        self._filters.append(("in", field, values))
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def order(self, field: str, desc: bool = False):
        self._order_field = field
        self._order_desc = desc
        return self

    def insert(self, payload):
        self._operation = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._operation = "update"
        self._payload = payload
        return self

    def execute(self):
        table = self.client.tables.setdefault(self.table_name, [])

        if self._operation == "insert":
            row = deepcopy(self._payload)
            table.append(row)
            return FakeResponse([row])

        rows = [row for row in table if self._matches(row)]

        if self._operation == "update":
            updated = []
            for row in rows:
                row.update(deepcopy(self._payload))
                updated.append(deepcopy(row))
            return FakeResponse(updated)

        rows = [deepcopy(row) for row in rows]
        if self._order_field:
            rows.sort(
                key=lambda item: item.get(self._order_field) or "",
                reverse=self._order_desc,
            )
        if self._limit is not None:
            rows = rows[: self._limit]
        return FakeResponse(rows)

    def _matches(self, row: dict) -> bool:
        for op, field, value in self._filters:
            if op == "eq" and row.get(field) != value:
                return False
            if op == "in" and row.get(field) not in value:
                return False
        return True


class FakeSupabaseClient:
    def __init__(self, tables: dict[str, list[dict]] | None = None):
        self.tables = deepcopy(tables or {})

    def table(self, table_name: str) -> FakeTableQuery:
        return FakeTableQuery(self, table_name)


class NormalizePhoneTests(unittest.TestCase):
    def test_remove_ddi_55_only_for_12_digits(self) -> None:
        self.assertEqual(normalize_phone("552234567890"), "2234567890")

    def test_remove_ddi_55_only_for_13_digits(self) -> None:
        self.assertEqual(normalize_phone("p:+5522999679925"), "22999679925")

    def test_keep_brazilian_ddd_55_when_number_has_11_digits(self) -> None:
        self.assertEqual(normalize_phone("55999999999"), "55999999999")

    def test_keep_number_without_ddi(self) -> None:
        self.assertEqual(normalize_phone("22999679925"), "22999679925")


class ParseMetaFieldDataTests(unittest.TestCase):
    def test_parse_contact_and_custom_fields_conservatively(self) -> None:
        parsed = _parse_meta_field_data(
            [
                {"name": "full_name", "values": ["Maria da Silva"]},
                {"name": "email", "values": ["maria@example.com"]},
                {"name": "phone_number", "values": ["p:+5522999679925"]},
                {"name": "objetivo_consorcio", "values": ["Comprar imóvel"]},
                {"name": "valor_mensal_pretendido", "values": ["R$ 800"]},
                {"name": "renda_mensal", "values": ["R$ 5.000"]},
            ]
        )

        self.assertEqual(parsed["nome"], "Maria da Silva")
        self.assertEqual(parsed["email"], "maria@example.com")
        self.assertEqual(parsed["telefone"], "22999679925")
        self.assertEqual(parsed["custom_fields"]["objetivo_consorcio_raw"], "Comprar imóvel")
        self.assertEqual(parsed["custom_fields"]["valor_mensal_pretendido_raw"], "R$ 800")
        self.assertEqual(parsed["custom_fields"]["renda_mensal_raw"], "R$ 5.000")
        self.assertEqual(parsed["raw_field_values"]["phone_number"], "p:+5522999679925")


class MetaDiagnosticMergeTests(unittest.TestCase):
    def test_merge_meta_extras_without_overwriting_manual_diagnostic(self) -> None:
        context = _build_meta_ads_context(
            leadgen_id="leadgen-1",
            form_id="form-1",
            campaign_name="Campanha Imóveis",
            adset_name="Conjunto Centro",
            ad_name="Anúncio 01",
            form_name="Formulário Imóveis",
            platform="instagram",
            raw_field_values={"objetivo_consorcio": "alavancagem_patrimonial"},
            custom_fields={
                "objetivo_consorcio_raw": "alavancagem_patrimonial",
                "valor_mensal_pretendido_raw": "r$_2.000_a_r$_5.000",
                "renda_mensal_raw": "r$10.000_a_r$30.000",
            },
        )

        payload = _build_meta_diagnostic_payload(
            existing_record={
                "id": "diag-1",
                "objetivo": "Compra manual",
                "extras": {
                    "comentarios": "diagnóstico manual",
                    "meta_ads": {
                        "form_answers": {
                            "objetivo_consorcio_label": "Objetivo anterior",
                        }
                    },
                },
            },
            org_id="org-1",
            lead_id="lead-1",
            meta_ads_context=context,
        )

        self.assertNotIn("objetivo", payload)
        self.assertEqual(payload["extras"]["comentarios"], "diagnóstico manual")
        self.assertEqual(
            payload["extras"]["meta_ads"]["form_answers"]["objetivo_consorcio_label"],
            "Alavancagem patrimonial",
        )
        self.assertEqual(
            payload["extras"]["meta_ads"]["form_answers"]["valor_mensal_pretendido_label"],
            "R$ 2.000 a R$ 5.000",
        )
        self.assertEqual(
            payload["extras"]["meta_ads"]["form_answers"]["renda_mensal_label"],
            "R$ 10.000 a R$ 30.000",
        )

    def test_create_minimal_lead_diagnostic_when_missing(self) -> None:
        supa = FakeSupabaseClient({"lead_diagnosticos": []})

        saved = upsert_meta_diagnostic_from_meta(
            supa,
            org_id="org-1",
            lead_id="lead-1",
            leadgen_id="meta-lead-1",
            form_id="form-1",
            lead_data={
                "form_id": "form-1",
                "campaign_name": "Campanha Imóveis",
                "adset_name": "Conjunto Centro",
                "ad_name": "Anúncio 01",
                "form_name": "Formulário Imóveis",
                "platform": "instagram",
            },
            custom_fields={
                "field_values": {"objetivo_consorcio": "alavancagem_patrimonial"},
                "objetivo_consorcio_raw": "alavancagem_patrimonial",
                "valor_mensal_pretendido_raw": "r$_2.000_a_r$_5.000",
                "renda_mensal_raw": "r$10.000_a_r$30.000",
            },
        )

        self.assertEqual(saved["org_id"], "org-1")
        self.assertEqual(saved["lead_id"], "lead-1")
        self.assertEqual(saved["objetivo"], "Alavancagem patrimonial")
        self.assertEqual(saved["extras"]["meta_ads"]["campaign_name"], "Campanha Imóveis")
        self.assertEqual(saved["extras"]["meta_ads"]["adset_name"], "Conjunto Centro")
        self.assertEqual(saved["extras"]["meta_ads"]["ad_name"], "Anúncio 01")
        self.assertEqual(saved["extras"]["meta_ads"]["form_name"], "Formulário Imóveis")
        self.assertEqual(saved["extras"]["meta_ads"]["platform"], "instagram")
        self.assertEqual(
            saved["extras"]["meta_ads"]["form_answers"]["valor_mensal_pretendido_label"],
            "R$ 2.000 a R$ 5.000",
        )
        self.assertEqual(saved["extras"]["meta_ads"]["leadgen_id"], "meta-lead-1")


class KanbanMetaFieldsTests(unittest.TestCase):
    def test_return_meta_fields_in_kanban_snapshot(self) -> None:
        supa = FakeSupabaseClient(
            {
                "leads": [
                    {
                        "id": "lead-1",
                        "org_id": "org-1",
                        "nome": "Maria da Silva",
                        "etapa": "novo",
                        "telefone": "22999679925",
                        "email": "maria@example.com",
                        "origem": "meta_ads",
                        "owner_id": None,
                        "created_at": "2026-05-04T12:00:00+00:00",
                        "first_contact_at": None,
                        "source_label": "Meta Ads",
                        "form_label": "Formulário Imóveis",
                        "channel": "instagram",
                        "utm_campaign": "Campanha Imóveis",
                        "utm_term": "Conjunto Centro",
                        "utm_content": "Anúncio 01",
                    }
                ],
                "lead_interesses": [],
                "lead_diagnosticos": [
                    {
                        "lead_id": "lead-1",
                        "org_id": "org-1",
                        "readiness_score": 72,
                        "score_risco": 18,
                        "prob_conversao": 0.61,
                        "objetivo": "Alavancagem patrimonial",
                        "extras": {
                            "meta_ads": {
                                "form_answers": {
                                    "objetivo_consorcio_label": "Alavancagem patrimonial",
                                    "valor_mensal_pretendido_label": "R$ 2.000 a R$ 5.000",
                                    "renda_mensal_label": "R$ 10.000 a R$ 30.000",
                                },
                                "leadgen_id": "meta-lead-1",
                                "platform": "instagram",
                                "campaign_name": "Campanha Imóveis",
                                "adset_name": "Conjunto Centro",
                                "ad_name": "Anúncio 01",
                                "form_name": "Formulário Imóveis",
                            }
                        },
                    }
                ],
            }
        )

        snapshot = build_kanban_snapshot("org-1", supa)
        card = snapshot.columns["novo"][0]

        self.assertEqual(card.source_label, "Meta Ads")
        self.assertEqual(card.form_label, "Formulário Imóveis")
        self.assertEqual(card.channel, "instagram")
        self.assertEqual(card.utm_campaign, "Campanha Imóveis")
        self.assertEqual(card.utm_term, "Conjunto Centro")
        self.assertEqual(card.utm_content, "Anúncio 01")
        self.assertEqual(card.meta_ads_summary.platform, "instagram")
        self.assertEqual(card.meta_ads_summary.leadgen_id, "meta-lead-1")
        self.assertEqual(card.meta_ads_summary.ad_name, "Anúncio 01")
        self.assertEqual(card.meta_ads_summary.campaign_name, "Campanha Imóveis")
        self.assertEqual(
            card.meta_ads_form_answers["objetivo_consorcio_label"],
            "Alavancagem patrimonial",
        )

    def test_kanban_snapshot_keeps_safe_fallback_when_extras_is_empty(self) -> None:
        supa = FakeSupabaseClient(
            {
                "leads": [
                    {
                        "id": "lead-2",
                        "org_id": "org-1",
                        "nome": "Lead sem extras",
                        "etapa": "novo",
                        "telefone": "22999999999",
                        "email": None,
                        "origem": "meta_ads",
                        "owner_id": None,
                        "created_at": "2026-05-04T12:00:00+00:00",
                        "first_contact_at": None,
                        "source_label": None,
                        "form_label": None,
                        "channel": None,
                        "utm_campaign": None,
                        "utm_term": None,
                        "utm_content": None,
                    }
                ],
                "lead_interesses": [],
                "lead_diagnosticos": [
                    {
                        "lead_id": "lead-2",
                        "org_id": "org-1",
                        "readiness_score": 10,
                        "score_risco": 5,
                        "prob_conversao": 0.12,
                        "objetivo": None,
                        "extras": {},
                    }
                ],
            }
        )

        snapshot = build_kanban_snapshot("org-1", supa)
        card = snapshot.columns["novo"][0]

        self.assertIsNone(card.meta_ads_summary)
        self.assertIsNone(card.meta_ads_form_answers)


if __name__ == "__main__":
    unittest.main()
