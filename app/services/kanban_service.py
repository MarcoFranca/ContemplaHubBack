# app/services/kanban_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import Client

from app.schemas.kanban import KanbanSnapshot, LeadCard, Stage, KanbanMetrics, Interest


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
    Monta o snapshot de Kanban a partir da tabela leads,
    enriquecendo com:
      - interesse aberto mais recente (lead_interesses)
      - scores de diagnóstico (lead_diagnosticos)
    """

    # 1) Quais etapas vamos buscar
    if not show_active and not show_lost:
        stages: List[Stage] = ["novo", "diagnostico", "proposta", "negociacao", "contrato"]
    elif show_active and not show_lost:
        stages = ["ativo"]
    elif show_lost and not show_active:
        stages = ["perdido"]
    else:
        stages = ["ativo", "perdido"]

    # 2) Busca os leads da organização nessas etapas
    resp = (
        supa.table("leads")
        .select(
            "id, nome, etapa, telefone, email, origem, owner_id, created_at, first_contact_at"
        )
        .eq("org_id", org_id)
        .in_("etapa", stages)
        .execute()
    )
    rows: List[Dict[str, Any]] = resp.data or []

    columns = _empty_columns()

    if not rows:
        return KanbanSnapshot(columns=columns)

    # ---------------------------------------------------------
    # 3) Descobre todos os lead_ids envolvidos
    # ---------------------------------------------------------
    lead_ids = [r["id"] for r in rows if r.get("id")]

    # ---------------------------------------------------------
    # 4) Busca interesse aberto mais recente em lead_interesses
    # ---------------------------------------------------------
    interests_by_lead: Dict[str, Interest] = {}

    if lead_ids:
        i_resp = (
            supa.table("lead_interesses")
            .select(
                "lead_id, produto, valor_total, prazo_meses, objetivo, perfil_desejado, observacao, created_at, status"
            )
            .in_("lead_id", lead_ids)
            .eq("status", "aberto")
            .order("created_at", desc=True)
            .execute()
        )
        i_rows: List[Dict[str, Any]] = i_resp.data or []

        for r in i_rows:
            lid = r.get("lead_id")
            # como ordenamos por created_at desc, o primeiro que cair aqui é o mais recente
            if not lid or lid in interests_by_lead:
                continue

            interests_by_lead[lid] = Interest(
                produto=r.get("produto"),
                # front espera string; deixamos simples (ex.: "300000").
                valorTotal=str(r.get("valor_total")) if r.get("valor_total") is not None else None,
                prazoMeses=r.get("prazo_meses"),
                objetivo=r.get("objetivo"),
                perfilDesejado=r.get("perfil_desejado"),
                observacao=r.get("observacao"),
            )

    # ---------------------------------------------------------
    # 5) Busca diagnóstico atual em lead_diagnosticos
    # ---------------------------------------------------------
    diag_by_lead: Dict[str, Dict[str, Any]] = {}

    if lead_ids:
        d_resp = (
            supa.table("lead_diagnosticos")
            .select(
                "lead_id, readiness_score, score_risco, prob_conversao"
            )
            .eq("org_id", org_id)
            .in_("lead_id", lead_ids)
            .execute()
        )
        d_rows: List[Dict[str, Any]] = d_resp.data or []

        for r in d_rows:
            lid = r.get("lead_id")
            if not lid:
                continue
            # só 1 por lead (upsert manual garante),
            # se vier mais de um, o último sobrescreve.
            diag_by_lead[lid] = {
                "readiness_score": r.get("readiness_score"),
                "score_risco": r.get("score_risco"),
                "prob_conversao": r.get("prob_conversao"),
            }

    # ---------------------------------------------------------
    # 6) Monta as colunas já com interest + diagnóstico
    # ---------------------------------------------------------
    for row in rows:
        etapa = row.get("etapa")
        if etapa not in columns:
            continue

        lid = row["id"]

        interest = interests_by_lead.get(lid)
        diag = diag_by_lead.get(lid) or {}

        card = LeadCard(
            id=lid,
            nome=row.get("nome") or "Sem nome",
            etapa=etapa,
            telefone=row.get("telefone"),
            email=row.get("email"),
            origem=row.get("origem"),
            owner_id=row.get("owner_id"),
            created_at=row.get("created_at"),
            first_contact_at=row.get("first_contact_at"),
            interest=interest,
            readiness_score=diag.get("readiness_score"),
            score_risco=diag.get("score_risco"),
            prob_conversao=diag.get("prob_conversao"),
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

    # Outras regras futuras (contrato, ativo, perdido, etc.) entram aqui
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

    # TODO: se quiser guardar reason em alguma coluna de observação / histórico, tratar aqui

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
