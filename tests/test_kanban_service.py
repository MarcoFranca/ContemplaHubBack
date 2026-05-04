import unittest
from copy import deepcopy

from app.services.kanban_service import build_kanban_snapshot, move_lead_stage


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTableQuery:
    def __init__(self, client: "FakeSupabaseClient", table_name: str):
        self.client = client
        self.table_name = table_name
        self._filters: list[tuple[str, str, object]] = []
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

    def update(self, payload):
        self._operation = "update"
        self._payload = payload
        return self

    def maybe_single(self):
        return self

    def execute(self):
        table = self.client.tables.setdefault(self.table_name, [])
        rows = [row for row in table if self._matches(row)]

        if self._operation == "update":
            updated = []
            for row in rows:
                row.update(deepcopy(self._payload))
                updated.append(deepcopy(row))
            return FakeResponse(updated)

        rows = [deepcopy(row) for row in rows]
        if self._operation == "select" and self.client.single_mode:
            return FakeResponse(rows[0] if rows else None)
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
        self.single_mode = False

    def table(self, table_name: str) -> FakeTableQuery:
        self.single_mode = False
        query = FakeTableQuery(self, table_name)
        original_maybe_single = query.maybe_single

        def _maybe_single():
            self.single_mode = True
            return original_maybe_single()

        query.maybe_single = _maybe_single
        return query


class KanbanStageSnapshotTests(unittest.TestCase):
    def test_snapshot_defaults_to_main_funnel_only(self) -> None:
        supa = FakeSupabaseClient(
            {
                "leads": [
                    {"id": "lead-1", "org_id": "org-1", "nome": "Novo", "etapa": "novo"},
                    {"id": "lead-2", "org_id": "org-1", "nome": "Tentativa", "etapa": "tentativa_contato"},
                    {"id": "lead-3", "org_id": "org-1", "nome": "Contato", "etapa": "contato_realizado"},
                    {"id": "lead-4", "org_id": "org-1", "nome": "Pós-venda", "etapa": "pos_venda"},
                    {"id": "lead-5", "org_id": "org-1", "nome": "Frio", "etapa": "frio"},
                    {"id": "lead-6", "org_id": "org-1", "nome": "Perdido", "etapa": "perdido"},
                ],
                "lead_interesses": [],
                "lead_diagnosticos": [],
            }
        )

        snapshot = build_kanban_snapshot("org-1", supa)

        self.assertEqual(len(snapshot.columns["novo"]), 1)
        self.assertEqual(len(snapshot.columns["tentativa_contato"]), 1)
        self.assertEqual(len(snapshot.columns["contato_realizado"]), 1)
        self.assertEqual(len(snapshot.columns["pos_venda"]), 0)
        self.assertEqual(len(snapshot.columns["frio"]), 0)
        self.assertEqual(len(snapshot.columns["perdido"]), 0)

    def test_snapshot_includes_supplemental_stages_when_filters_are_enabled(self) -> None:
        supa = FakeSupabaseClient(
            {
                "leads": [
                    {"id": "lead-4", "org_id": "org-1", "nome": "Pós-venda", "etapa": "pos_venda"},
                    {"id": "lead-5", "org_id": "org-1", "nome": "Frio", "etapa": "frio"},
                    {"id": "lead-6", "org_id": "org-1", "nome": "Perdido", "etapa": "perdido"},
                ],
                "lead_interesses": [],
                "lead_diagnosticos": [],
            }
        )

        snapshot = build_kanban_snapshot(
            "org-1",
            supa,
            show_active=True,
            show_lost=True,
            show_cold=True,
        )

        self.assertEqual(snapshot.columns["pos_venda"][0].nome, "Pós-venda")
        self.assertEqual(snapshot.columns["frio"][0].nome, "Frio")
        self.assertEqual(snapshot.columns["perdido"][0].nome, "Perdido")


class MoveLeadStageTests(unittest.TestCase):
    def test_move_stage_marks_first_contact_when_leaving_novo(self) -> None:
        supa = FakeSupabaseClient(
            {
                "leads": [
                    {
                        "id": "lead-1",
                        "org_id": "org-1",
                        "etapa": "novo",
                        "first_contact_at": None,
                    }
                ]
            }
        )

        result = move_lead_stage(
            org_id="org-1",
            lead_id="lead-1",
            new_stage="tentativa_contato",
            supa=supa,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["lead"]["etapa"], "tentativa_contato")
        self.assertIsNotNone(result["lead"]["first_contact_at"])

    def test_move_stage_accepts_frio_as_valid_terminal_state(self) -> None:
        supa = FakeSupabaseClient(
            {
                "leads": [
                    {
                        "id": "lead-2",
                        "org_id": "org-1",
                        "etapa": "tentativa_contato",
                        "first_contact_at": "2026-05-04T12:00:00+00:00",
                    }
                ]
            }
        )

        result = move_lead_stage(
            org_id="org-1",
            lead_id="lead-2",
            new_stage="frio",
            supa=supa,
            reason="Sem resposta após tentativas.",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["lead"]["etapa"], "frio")


if __name__ == "__main__":
    unittest.main()
