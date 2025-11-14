# app/services/kanban_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from supabase import Client

from app.schemas.kanban import KanbanSnapshot, LeadCard, Stage, KanbanMetrics


def _empty_columns() -> Dict[Stage, List[LeadCard]]:
    return {
        "novo": [],
        "diagnostico": [],
        "proposta": [],
        "negociacao": [],
        "contrato": [],
        "ativo": [],
        "perdido": [],
    }


def build_kanban_snapshot(
    org_id: str,
    supa: Client,
    show_active: bool = False,
    show_lost: bool = False,
) -> KanbanSnapshot:
    """
    Monta o snapshot de Kanban a partir da tabela leads.

    Regras de filtro:
    - Nenhum flag (padrão)       -> etapas de funil: novo..contrato
    - show_active=true           -> somente 'ativo'
    - show_lost=true             -> somente 'perdido'
    - show_active=true & show_lost=true -> 'ativo' + 'perdido'
    """

    # Definir quais etapas vamos buscar no banco
    if not show_active and not show_lost:
        stages: list[Stage] = ["novo", "diagnostico", "proposta", "negociacao", "contrato"]
    elif show_active and not show_lost:
        stages = ["ativo"]
    elif show_lost and not show_active:
        stages = ["perdido"]
    else:
        # show_active=true & show_lost=true
        stages = ["ativo", "perdido"]

    query = (
        supa.table("leads")
        .select(
            "id, nome, etapa, telefone, email, origem, owner_id, created_at, first_contact_at"
        )
        .eq("org_id", org_id)
        .in_("etapa", stages)
    )

    resp = query.execute()
    rows = resp.data or []

    columns = _empty_columns()

    for row in rows:
        etapa = row.get("etapa")
        if etapa not in columns:
            continue

        card = LeadCard(
            id=row["id"],
            nome=row.get("nome") or "Sem nome",
            etapa=etapa,
            telefone=row.get("telefone"),
            email=row.get("email"),
            origem=row.get("origem"),
            owner_id=row.get("owner_id"),
            created_at=row.get("created_at"),
            first_contact_at=row.get("first_contact_at"),
        )
        columns[etapa].append(card)

    return KanbanSnapshot(columns=columns)


def _apply_stage_business_rules(
    current_lead: dict,
    new_stage: Stage,
) -> dict:
    """
    Retorna o payload de update na tabela 'leads'
    aplicando regras de negócio básicas para mudança de etapa.
    """
    updates: dict = {"etapa": new_stage}

    from_stage = current_lead.get("etapa")
    first_contact_at = current_lead.get("first_contact_at")

    # Regra 1: se sair de "novo" (para qualquer outra etapa) pela 1ª vez, marca first_contact_at
    if from_stage == "novo" and not first_contact_at and new_stage != "novo":
        updates["first_contact_at"] = datetime.now(timezone.utc).isoformat()

    # Aqui adicionamos outras regras no futuro (contrato, ativo, perdido, etc.)
    return updates


def move_lead_stage(
    org_id: str,
    lead_id: str,
    new_stage: Stage,
    supa: Client,
    reason: Optional[str] = None,
) -> dict:
    """
    Muda a etapa de um lead, aplicando regras de negócio e
    garantindo que o lead pertence ao org_id informado.
    """
    # 1) carrega lead atual
    #    traz também first_contact_at para as regras
    current = (
        supa.table("leads")
        .select("id, etapa, org_id, first_contact_at")
        .eq("id", lead_id)
        .maybe_single()
        .execute()
    )
    row = current.data
    if not row:
        return {
            "ok": False,
            "error": "not_found",
            "message": "Lead não encontrado",
        }

    if row["org_id"] != org_id:
        return {
            "ok": False,
            "error": "forbidden",
            "message": "Lead de outra organização",
        }

    if row["etapa"] == new_stage:
        return {
            "ok": True,
            "skipped": True,
            "message": "Etapa já está no valor solicitado",
        }

    # 2) aplica regras de negócio
    updates = _apply_stage_business_rules(row, new_stage)

    # TODO: se quiser guardar reason em alguma coluna de observação / histórico, tratamos aqui

    # 3) atualiza no Supabase
    upd_resp = (
        supa.table("leads")
        .update(updates)
        .eq("id", lead_id)
        .execute()
    )
    rows = upd_resp.data or []
    if not rows:
        return {
            "ok": False,
            "error": "update_failed",
            "message": "Falha ao atualizar etapa",
        }

    lead = rows[0]

    # TODO: se o histórico de etapas e outbox estiver por trigger, beleza.
    # Se não, aqui é o lugar de inserir em lead_stage_history e event_outbox.

    return {"ok": True, "lead": lead}


def get_kanban_metrics(
    org_id: str,
    supa: Client,
) -> KanbanMetrics:
    """
    Lê as métricas do Kanban usando a função SQL get_kanban_metrics(p_org uuid).

    Faz parsing defensivo, pois o Supabase pode retornar:
    - uma lista com 1 dict
    - uma lista com várias linhas
    - ou um dict direto (jsonb)
    """
    resp = supa.rpc("get_kanban_metrics", {"p_org": org_id}).execute()
    data: Any = resp.data

    # Normalizar para um dict "parsed"
    parsed: dict

    if isinstance(data, list):
        if not data:
            parsed = {}
        elif len(data) == 1 and isinstance(data[0], dict):
            # caso mais comum: 1 linha com as colunas/JSON
            parsed = data[0]
        else:
            # fallback: não sabemos o formato, devolvemos tudo em "rows"
            parsed = {"rows": data}
    elif isinstance(data, dict):
        parsed = data
    else:
        # qualquer outro tipo (string, número etc.)
        parsed = {"value": data}

    # Tentamos encontrar campos padrão com alguns nomes possíveis
    avg_days = (
        parsed.get("avgDays")
        or parsed.get("avg_days")
        or parsed.get("avg_days_by_stage")
        or None
    )
    conversion = (
        parsed.get("conversion")
        or parsed.get("conversion_by_stage")
        or None
    )
    diag_completion = (
        parsed.get("diagCompletionPct")
        or parsed.get("diag_completion_pct")
        or None
    )
    readiness_avg = (
        parsed.get("readinessAvg")
        or parsed.get("readiness_avg")
        or None
    )
    t_first_contact = (
        parsed.get("tFirstContactAvgMin")
        or parsed.get("tfirstcontact_avg_min")
        or None
    )

    return KanbanMetrics(
        avgDays=avg_days,
        conversion=conversion,
        diagCompletionPct=diag_completion,
        readinessAvg=readiness_avg,
        tFirstContactAvgMin=t_first_contact,
        raw=parsed,
    )