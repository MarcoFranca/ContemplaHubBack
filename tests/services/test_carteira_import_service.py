from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from time import perf_counter
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers.carteira_import import _require_manager
from app.schemas.carteira_import import CarteiraImportPreviewRequest
from app.security.auth import CurrentProfile
from app.services import carteira_import_service as service


HEADERS = [
    "sistema",
    "Lance feito",
    "CONTEMPLADA",
    "optin",
    "Cliente",
    "TIPO DE LANCE",
    "empresa",
    "valor da cota",
    "grupo",
    "cota",
    "prazo",
    "forma de pagamento",
    "indice de coreção",
    "furo",
    "objetivo",
    "estrategia / obs",
    "Parcela reduzida",
    "data ultimo lance",
    "detalhes lance",
    "APORTE",
    "VALOR FINAL DA CARTA",
    "VALOR DA PARCELA",
]


def make_row(**overrides: str) -> str:
    values = {
        "sistema": "imobiliario",
        "Lance feito": "",
        "CONTEMPLADA": "",
        "optin": "",
        "Cliente": "Maria da Silva",
        "TIPO DE LANCE": "",
        "empresa": "Rodobens",
        "valor da cota": "R$ 300.000,00",
        "grupo": "1001",
        "cota": "55",
        "prazo": "180",
        "forma de pagamento": "boleto",
        "indice de coreção": "INCC",
        "furo": "0",
        "objetivo": "Casa própria",
        "estrategia / obs": "",
        "Parcela reduzida": "",
        "data ultimo lance": "",
        "detalhes lance": "",
        "APORTE": "",
        "VALOR FINAL DA CARTA": "",
        "VALOR DA PARCELA": "1.500,00",
    }
    values.update(overrides)
    return "\t".join(values[header] for header in HEADERS)


def build_tsv(*rows: str) -> str:
    return "\n".join(["\t".join(HEADERS), *rows])


def build_csv(*rows: list[str]) -> str:
    rendered_rows = []
    for row in rows:
        escaped = []
        for value in row:
            text = value or ""
            if any(token in text for token in [",", '"', "\n"]):
                text = '"' + text.replace('"', '""') + '"'
            escaped.append(text)
        rendered_rows.append(",".join(escaped))
    return "\n".join([",".join(HEADERS), *rendered_rows])


def make_separator_row(token: str = "---") -> str:
    return "\t".join([token] * len(HEADERS))


class FakeResponse:
    def __init__(self, data: Any):
        self.data = data


class FakeTableQuery:
    def __init__(self, client: "FakeSupabaseClient", table_name: str):
        self.client = client
        self.table_name = table_name
        self._filters: list[tuple[str, str, object]] = []
        self._or_filters: list[list[tuple[str, str, object]]] = []
        self._limit: int | None = None
        self._operation = "select"
        self._payload: Any = None

    def select(self, _columns: str):
        self._operation = "select"
        return self

    def eq(self, field: str, value: object):
        self._filters.append(("eq", field, value))
        return self

    def ilike(self, field: str, value: str):
        self._filters.append(("ilike", field, value))
        return self

    def or_(self, expression: str):
        branches: list[tuple[str, str, object]] = []
        for raw_clause in expression.split(","):
            clause = raw_clause.strip()
            if ".eq." in clause:
                field, expected = clause.split(".eq.", 1)
                branches.append(("eq", field, expected))
            elif clause.endswith(".is.null"):
                field = clause[: -len(".is.null")]
                branches.append(("is_null", field, None))
        self._or_filters.append(branches)
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def insert(self, payload: Any, returning: str | None = None):
        del returning
        self._operation = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict[str, Any]):
        self._operation = "update"
        self._payload = payload
        return self

    def execute(self):
        self.client.query_count += 1
        table = self.client.tables.setdefault(self.table_name, [])

        if self._operation == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            inserted = []
            for payload in payloads:
                row = deepcopy(payload)
                row.setdefault("id", f"{self.table_name}-{len(table) + 1}")
                table.append(row)
                inserted.append(deepcopy(row))
            return FakeResponse(inserted)

        matched = [row for row in table if self._matches(row)]

        if self._operation == "update":
            updated = []
            for row in matched:
                row.update(deepcopy(self._payload))
                updated.append(deepcopy(row))
            return FakeResponse(updated)

        rows = [deepcopy(row) for row in matched]
        if self._limit is not None:
            rows = rows[: self._limit]
        return FakeResponse(rows)

    def _matches(self, row: dict[str, Any]) -> bool:
        for op, field, value in self._filters:
            current = row.get(field)
            if op == "eq" and current != value:
                return False
            if op == "ilike":
                pattern = str(value).strip("%").lower()
                if pattern not in str(current or "").lower():
                    return False

        for branches in self._or_filters:
            if not any(self._match_branch(row, branch) for branch in branches):
                return False

        return True

    @staticmethod
    def _match_branch(row: dict[str, Any], branch: tuple[str, str, object]) -> bool:
        op, field, value = branch
        current = row.get(field)
        if op == "eq":
            return current == value
        if op == "is_null":
            return current in (None, "")
        return False


class FakeSupabaseClient:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None):
        self.tables = deepcopy(tables or {})
        self.query_count = 0

    def table(self, table_name: str) -> FakeTableQuery:
        return FakeTableQuery(self, table_name)


@pytest.fixture
def profile() -> CurrentProfile:
    return CurrentProfile(user_id="user-1", org_id="org-1", role="admin")


@pytest.fixture
def fake_sb() -> FakeSupabaseClient:
    return FakeSupabaseClient(
        {
            "leads": [],
            "administradoras": [],
            "grupos": [],
            "cotas": [],
            "contratos": [],
            "lances": [],
            "contemplacoes": [],
            "carteira_clientes": [],
        }
    )


@pytest.fixture(autouse=True)
def service_patches(monkeypatch: pytest.MonkeyPatch):
    def fake_ensure_carteira_cliente(*, supa, org_id: str, lead_id: str, origem_entrada: str, observacoes: str):
        table = supa.tables.setdefault("carteira_clientes", [])
        existing = next(
            (row for row in table if row.get("org_id") == org_id and row.get("lead_id") == lead_id),
            None,
        )
        if existing:
            return {"created": False, "carteira_cliente": deepcopy(existing)}

        row = {
            "id": f"carteira_clientes-{len(table) + 1}",
            "org_id": org_id,
            "lead_id": lead_id,
            "origem_entrada": origem_entrada,
            "observacoes": observacoes,
        }
        table.append(row)
        return {"created": True, "carteira_cliente": deepcopy(row)}

    monkeypatch.setattr(service, "ensure_carteira_cliente", fake_ensure_carteira_cliente)
    monkeypatch.setattr(service, "apply_lead_address_rules", lambda payload: payload)
    monkeypatch.setattr(service, "normalize_cota_financial_payload", lambda payload: payload)


def test_parse_import_rows_ignores_empty_lines_in_preview(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(make_row(), "", "", make_row(Cliente="João da Silva", cota="56"))

    parsed_rows = service.parse_import_rows(raw_text, produto_padrao="imobiliario")
    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert len(parsed_rows) == 4
    assert preview.summary.ignoradas == 2
    assert len(preview.rows) == 2
    assert [row.cliente_nome for row in preview.rows] == ["Maria da Silva", "João da Silva"]


def test_parse_import_rows_ignores_separator_lines_in_preview(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    separator = make_separator_row("---")
    raw_text = build_tsv(make_row(), separator, make_row(Cliente="Cliente Dois", cota="57"))

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert preview.summary.ignoradas == 1
    assert len(preview.rows) == 2
    assert all(row.cliente_nome != "---" for row in preview.rows)


def test_parse_import_rows_accepts_csv_with_quoted_money_fields(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_csv(
        [
            "FALSE",
            "TRUE",
            "FALSE",
            "TRUE",
            "MARINA DA COSTA",
            "FIXO",
            "Rodobens",
            "R$ 1.000.000,00",
            "1880",
            "344",
            "216",
            "BOLETO",
            "INCC",
            "6",
            "Compra de imóvel",
            "usar lance fixo com embutido",
            "30%",
            "10/09/2025",
            "40%",
            "R$ 100.000,00",
            "R$ 700.000,00",
            "R$ 4.581,34",
        ]
    )

    parsed = service.parse_import_rows(raw_text, produto_padrao="imobiliario")
    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert len(parsed) == 1
    assert parsed[0].cliente_nome == "MARINA DA COSTA"
    assert parsed[0].valor_carta == Decimal("1000000.00")
    assert parsed[0].valor_parcela == Decimal("4581.34")
    assert preview.summary.total_rows == 1
    assert len(preview.rows) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("R$ 1.000.000,00", Decimal("1000000.00")),
        ("1000000,00", Decimal("1000000.00")),
        ("1.000.000", Decimal("1000000")),
    ],
)
def test_parse_decimal_normalizes_brazilian_money(raw: str, expected: Decimal):
    assert service._parse_decimal(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("40%", Decimal("40")),
        ("40,5%", Decimal("40.5")),
    ],
)
def test_parse_percent_normalizes_brazilian_percent(raw: str, expected: Decimal):
    assert service._parse_percent(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TRUE", True),
        ("FALSE", False),
        ("SIM", True),
        ("NÃO", False),
        ("", None),
    ],
)
def test_parse_bool_accepts_expected_values(raw: str, expected: bool | None):
    assert service._parse_bool(raw) is expected


def test_normalize_name_removes_extra_whitespace_and_is_case_insensitive():
    raw = "  Maria \n  da   SILVA  "

    assert service._normalize_text(raw) == "Maria da SILVA"
    assert service._normalize_lookup(raw) == "maria da silva"
    assert service._normalize_lookup("maria da silva") == service._normalize_lookup(raw)


def test_preview_plans_single_client_for_same_name_in_lote(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(cota="55"),
        make_row(cota="56", grupo="1002"),
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert preview.summary.clientes_a_criar == 1
    assert len(preview.rows) == 2


def test_confirm_reuses_single_client_for_same_name_in_lote(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(cota="55"),
        make_row(cota="56", grupo="1002"),
    )

    result = service.confirm_import(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert result.imported_rows == 2
    assert len(fake_sb.tables["leads"]) == 1
    lead_ids = {row.lead_id for row in result.rows if row.status in {"pronta", "aviso"}}
    assert len(lead_ids) == 1


def test_preview_plans_single_administradora_and_two_grupos(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(grupo="1001", cota="55"),
        make_row(grupo="1002", cota="56"),
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert preview.summary.administradoras_a_criar == 1
    assert preview.summary.grupos_a_criar == 2


def test_confirm_reuses_single_administradora_and_creates_two_grupos(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(grupo="1001", cota="55"),
        make_row(grupo="1002", cota="56"),
    )

    result = service.confirm_import(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert result.imported_rows == 2
    assert len(fake_sb.tables["administradoras"]) == 1
    assert len(fake_sb.tables["grupos"]) == 2


def test_preview_does_not_duplicate_same_group_in_lote(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(grupo="1001", cota="55"),
        make_row(grupo="1001", cota="56"),
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert preview.summary.grupos_a_criar == 1


def test_confirm_does_not_duplicate_same_group_in_lote(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(grupo="1001", cota="55"),
        make_row(grupo="1001", cota="56"),
    )

    result = service.confirm_import(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert result.imported_rows == 2
    assert len(fake_sb.tables["grupos"]) == 1


def test_preview_marks_duplicate_cota_in_lote_as_error(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(grupo="1001", cota="55"),
        make_row(grupo="1001", cota="55"),
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    erro_rows = [row for row in preview.rows if row.status == "erro"]
    assert len(erro_rows) == 1
    assert "repete a cota" in erro_rows[0].errors[0]


def test_confirm_does_not_create_duplicate_cota_for_error_row(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(grupo="1001", cota="55"),
        make_row(grupo="1001", cota="55"),
    )

    result = service.confirm_import(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert result.imported_rows == 1
    assert result.failed_rows == 1
    assert len(fake_sb.tables["cotas"]) == 1


def test_preview_prepares_valid_fixo_lance(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(
            **{
                "Lance feito": "TRUE",
                "TIPO DE LANCE": "FIXO",
                "data ultimo lance": "10/09/2025",
                "detalhes lance": "40%",
            }
        )
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert preview.rows[0].planned.lance_criar is True


def test_preview_prepares_valid_livre_lance(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(
            **{
                "Lance feito": "SIM",
                "TIPO DE LANCE": "LIVRE",
                "data ultimo lance": "10/09/2025",
                "detalhes lance": "R$ 12.000,00",
            }
        )
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert preview.rows[0].planned.lance_criar is True


def test_preview_warns_for_sorteio_without_creating_lance(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(
            **{
                "TIPO DE LANCE": "SORTEIO",
                "Lance feito": "TRUE",
                "data ultimo lance": "10/09/2025",
                "detalhes lance": "40%",
            }
        )
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    row = preview.rows[0]
    assert row.planned.lance_criar is False
    assert any("SORTEIO" in warning for warning in row.warnings)
    assert row.status == "aviso"


def test_preview_prepares_contemplacao_for_true_flag(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(
            **{
                "CONTEMPLADA": "TRUE",
                "data ultimo lance": "10/09/2025",
            }
        )
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert preview.rows[0].planned.contemplacao_criar is True


def test_preview_warns_when_contemplacao_already_exists(profile: CurrentProfile):
    fake_sb = FakeSupabaseClient(
        {
            "leads": [{"id": "lead-1", "org_id": "org-1", "nome": "Maria da Silva"}],
            "administradoras": [{"id": "adm-1", "org_id": "org-1", "nome": "Rodobens"}],
            "grupos": [{"id": "grupo-1", "org_id": "org-1", "administradora_id": "adm-1", "codigo": "1001"}],
            "cotas": [
                {
                    "id": "cota-1",
                    "org_id": "org-1",
                    "lead_id": "lead-1",
                    "administradora_id": "adm-1",
                    "grupo_codigo": "1001",
                    "numero_cota": "55",
                    "status": "contemplada",
                }
            ],
            "contemplacoes": [{"id": "cont-1", "org_id": "org-1", "cota_id": "cota-1", "data": "2025-09-10"}],
        }
    )
    raw_text = build_tsv(
        make_row(
            **{
                "CONTEMPLADA": "TRUE",
                "data ultimo lance": "10/09/2025",
            }
        )
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    row = preview.rows[0]
    assert row.planned.contemplacao_criar is False
    assert any("já possui contemplação" in warning for warning in row.warnings)


def test_preview_marks_partially_invalid_row_and_keeps_processing_other_rows(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(
        make_row(grupo="", cota=""),
        make_row(Cliente="Cliente Válido", grupo="1002", cota="56"),
    )

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert preview.summary.erros == 1
    assert preview.summary.prontas + preview.summary.avisos == 1
    assert any(row.cliente_nome == "Cliente Válido" for row in preview.rows)


def test_confirm_uses_same_parser_as_preview(profile: CurrentProfile, fake_sb: FakeSupabaseClient, monkeypatch: pytest.MonkeyPatch):
    original = service.parse_import_rows
    calls: list[str] = []

    def spy(raw_text: str, *, produto_padrao: service.ImportProduto):
        calls.append(raw_text)
        return original(raw_text, produto_padrao=produto_padrao)

    monkeypatch.setattr(service, "parse_import_rows", spy)
    raw_text = build_tsv(make_row(), make_row(Cliente="Cliente Dois", grupo="1002", cota="56"))

    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")
    confirm = service.confirm_import(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert calls == [raw_text, raw_text]
    preview_signature = [(row.cliente_nome, row.grupo_codigo, row.numero_cota, row.status) for row in preview.rows]
    confirm_signature = [(row.cliente_nome, row.cota_id is not None, row.status) for row in confirm.rows if row.status != "ignorada"]
    assert len(preview_signature) == len(confirm_signature)
    assert preview.summary.clientes_a_criar == 2
    assert confirm.imported_rows == 2


def test_import_request_schema_forbids_org_id_in_payload():
    with pytest.raises(ValidationError):
        CarteiraImportPreviewRequest.model_validate(
            {
                "raw_text": build_tsv(make_row()),
                "produto_padrao": "imobiliario",
                "org_id": "org-malicioso",
            }
        )


@pytest.mark.parametrize("role", ["admin", "gestor"])
def test_require_manager_accepts_only_admin_and_gestor(role: str):
    _require_manager(CurrentProfile(user_id="user-1", org_id="org-1", role=role))


@pytest.mark.parametrize("role", ["vendedor", "viewer"])
def test_require_manager_rejects_vendedor_and_viewer(role: str):
    with pytest.raises(HTTPException) as exc_info:
        _require_manager(CurrentProfile(user_id="user-1", org_id="org-1", role=role))

    assert exc_info.value.status_code == 403


def test_confirm_creates_rows_using_org_id_from_profile_only(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    raw_text = build_tsv(make_row())

    service.confirm_import(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")

    assert {row["org_id"] for row in fake_sb.tables["leads"]} == {"org-1"}
    assert {row["org_id"] for row in fake_sb.tables["administradoras"]} == {"org-1"}
    assert {row["org_id"] for row in fake_sb.tables["grupos"]} == {"org-1"}
    assert {row["org_id"] for row in fake_sb.tables["cotas"]} == {"org-1"}


def test_preview_basic_performance_with_200_rows(profile: CurrentProfile, fake_sb: FakeSupabaseClient):
    rows = []
    for index in range(200):
        rows.append(
            make_row(
                Cliente=f"Cliente {index % 20}",
                empresa=f"Administradora {index % 5}",
                grupo=f"G-{index % 10}",
                cota=f"C-{index % 25}",
            )
        )

    raw_text = build_tsv(*rows)
    started_at = perf_counter()
    _, preview = service.build_import_preview(sb=fake_sb, profile=profile, raw_text=raw_text, produto_padrao="imobiliario")
    elapsed = perf_counter() - started_at

    assert preview.summary.total_rows == 200
    assert preview.summary.erros > 0
    assert fake_sb.query_count < 1000
    assert elapsed < 2
