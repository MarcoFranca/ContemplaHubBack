# app/routers/carteira.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from supabase import Client

from app.deps import get_supabase_admin
from app.security.auth import CurrentProfile, get_current_profile

router = APIRouter(prefix="/v1/carteira", tags=["carteira"])

# Status "ativos" de contrato (ajuste se quiser)
ACTIVE_CONTRACT_STATUSES = ["pendente_assinatura", "pendente_pagamento", "alocado", "contemplado"]


def _normalize_q(q: str | None) -> str | None:
    if not q:
        return None
    qq = q.strip()
    return qq if qq else None


def _contract_status_filter(q, include_all: bool, status: str | None):
    """
    Se status veio explícito, usa ele.
    Senão:
      - include_all = false => só status ativos
      - include_all = true  => não filtra
    """
    if status:
        return q.eq("status", status)

    if include_all:
        return q

    return q.in_("status", ACTIVE_CONTRACT_STATUSES)


@router.get("/cartas")
def list_carteira_cartas(
    include_all: bool = Query(default=False, description="Se true, inclui contratos em qualquer status"),
    status: str | None = Query(default=None, description="Filtra por status do contrato (ignora include_all)"),
    produto: str | None = Query(default=None, description="Filtra por produto da cota"),
    owner_user_id: str | None = Query(default=None, description="Somente para gestor/admin: filtrar por owner"),
    q: str | None = Query(default=None, description="Busca por nome (MVP)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    sb: Client = Depends(get_supabase_admin),
    me: CurrentProfile = Depends(get_current_profile),
):
    """
    Visão flat: 1 linha por contrato/cota (cliente + cota + contrato).
    Default: só contratos "ativos" (ACTIVE_CONTRACT_STATUSES).
    """
    qq = _normalize_q(q)
    offset = (page - 1) * page_size

    # 1) Carrega contratos (org + status filter)
    qc = (
        sb.table("contratos")
        .select("id, lead_id, cota_id, status, data_assinatura, data_pagamento, data_alocacao, data_contemplacao, created_at")
        .eq("org_id", me.org_id)
    )

    qc = _contract_status_filter(qc, include_all, status)

    qc = qc.order("created_at", desc=True).range(offset, offset + page_size - 1)

    contratos_res = qc.execute()
    contratos = contratos_res.data or []

    if not contratos:
        return {
            "page": page,
            "page_size": page_size,
            "items": [],
            "meta": {"include_all": include_all, "status": status},
        }

    lead_ids = list({c["lead_id"] for c in contratos if c.get("lead_id")})
    cota_ids = list({c["cota_id"] for c in contratos if c.get("cota_id")})

    # 2) Carrega leads (com permissão)
    leads_map: dict[str, dict] = {}
    if lead_ids:
        ql = (
            sb.table("leads")
            .select("id, nome, telefone, email, owner_id")
            .in_("id", lead_ids)
            .eq("org_id", me.org_id)
        )

        if not me.is_manager:
            ql = ql.eq("owner_id", me.user_id)

        if owner_user_id and me.is_manager:
            ql = ql.eq("owner_id", owner_user_id)

        if qq:
            ql = ql.ilike("nome", f"%{qq}%")

        leads_res = ql.execute()
        for l in (leads_res.data or []):
            leads_map[l["id"]] = l

    # 3) Carrega cotas (filtra por produto se quiser)
    cotas_map: dict[str, dict] = {}
    if cota_ids:
        qct = (
            sb.table("cotas")
            .select("id, lead_id, produto, numero_cota, grupo_codigo, valor_carta, valor_parcela, prazo, assembleia_dia, created_at")
            .in_("id", cota_ids)
            .eq("org_id", me.org_id)
        )
        if produto:
            qct = qct.eq("produto", produto)

        cotas_res = qct.execute()
        for ct in (cotas_res.data or []):
            cotas_map[ct["id"]] = ct

    # 4) Monta items (remove contratos cujo lead não é visível pro usuário)
    items: list[dict] = []
    for c in contratos:
        lead = leads_map.get(c.get("lead_id"))
        if not lead:
            continue

        cota = cotas_map.get(c.get("cota_id"))
        # Se filtrou por produto e não bateu, cota some => não mostra item
        if produto and not cota:
            continue

        items.append(
            {
                "cliente": {
                    "lead_id": lead["id"],
                    "nome": lead.get("nome"),
                    "telefone": lead.get("telefone"),
                    "email": lead.get("email"),
                    "owner_user_id": lead.get("owner_id"),
                },
                "cota": {
                    "cota_id": cota.get("id") if cota else c.get("cota_id"),
                    "produto": cota.get("produto") if cota else None,
                    "numero_cota": cota.get("numero_cota") if cota else None,
                    "grupo_codigo": cota.get("grupo_codigo") if cota else None,
                    "valor_carta": cota.get("valor_carta") if cota else None,
                    "valor_parcela": cota.get("valor_parcela") if cota else None,
                    "prazo": cota.get("prazo") if cota else None,
                    "assembleia_dia": cota.get("assembleia_dia") if cota else None,
                },
                "contrato": {
                    "contrato_id": c["id"],
                    "status": c.get("status"),
                    "data_assinatura": c.get("data_assinatura"),
                    "data_pagamento": c.get("data_pagamento"),
                    "data_alocacao": c.get("data_alocacao"),
                    "data_contemplacao": c.get("data_contemplacao"),
                },
            }
        )

    return {
        "page": page,
        "page_size": page_size,
        "items": items,
        "meta": {"include_all": include_all, "status": status},
    }


@router.get("/clientes")
def list_carteira_clientes(
    include_all: bool = Query(default=False, description="Se true, inclui contratos em qualquer status"),
    status: str | None = Query(default=None, description="Filtra por status do contrato (ignora include_all)"),
    produto: str | None = Query(default=None, description="Filtra por produto da cota"),
    owner_user_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Busca por nome (MVP)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    sb: Client = Depends(get_supabase_admin),
    me: CurrentProfile = Depends(get_current_profile),
):
    """
    Visão agrupada:
      cliente + cartas[] (cotas ligadas aos contratos)
    Default: só contratos "ativos".
    """
    qq = _normalize_q(q)
    offset = (page - 1) * page_size

    # 1) Primeiro, pega contratos do org com filtro
    qc = (
        sb.table("contratos")
        .select("id, lead_id, cota_id, status, created_at")
        .eq("org_id", me.org_id)
    )
    qc = _contract_status_filter(qc, include_all, status)

    contratos_res = qc.execute()
    contratos = contratos_res.data or []
    if not contratos:
        return {
            "page": page,
            "page_size": page_size,
            "items": [],
            "meta": {"include_all": include_all, "status": status},
        }

    lead_ids_all = list({c["lead_id"] for c in contratos if c.get("lead_id")})
    cota_ids_all = list({c["cota_id"] for c in contratos if c.get("cota_id")})

    # 2) Carrega leads (com permissão) + pagina por nome
    ql = (
        sb.table("leads")
        .select("id, nome, telefone, email, owner_id")
        .in_("id", lead_ids_all)
        .eq("org_id", me.org_id)
    )

    if not me.is_manager:
        ql = ql.eq("owner_id", me.user_id)

    if owner_user_id and me.is_manager:
        ql = ql.eq("owner_id", owner_user_id)

    if qq:
        ql = ql.ilike("nome", f"%{qq}%")

    ql = ql.order("nome", desc=False).range(offset, offset + page_size - 1)

    leads_res = ql.execute()
    leads = leads_res.data or []
    lead_ids_page = [l["id"] for l in leads]

    if not lead_ids_page:
        return {
            "page": page,
            "page_size": page_size,
            "items": [],
            "meta": {"include_all": include_all, "status": status},
        }

    # 3) Filtra contratos só desses leads paginados
    contratos_page = [c for c in contratos if c.get("lead_id") in set(lead_ids_page)]
    cota_ids_page = list({c["cota_id"] for c in contratos_page if c.get("cota_id")})

    # 4) Carrega cotas dessas cotas (filtra produto)
    cotas_map: dict[str, dict] = {}
    if cota_ids_page:
        qct = (
            sb.table("cotas")
            .select("id, lead_id, produto, numero_cota, grupo_codigo, valor_carta, valor_parcela, prazo, assembleia_dia, created_at")
            .in_("id", cota_ids_page)
            .eq("org_id", me.org_id)
        )
        if produto:
            qct = qct.eq("produto", produto)

        cotas_res = qct.execute()
        for ct in (cotas_res.data or []):
            cotas_map[ct["id"]] = ct

    # 5) Agrupa cotas por lead (a partir dos contratos)
    cotas_by_lead: dict[str, list[dict]] = {}
    for c in contratos_page:
        cota_id = c.get("cota_id")
        if not cota_id:
            continue
        cota = cotas_map.get(cota_id)
        if produto and not cota:
            continue
        if cota:
            cotas_by_lead.setdefault(c["lead_id"], []).append(cota)

    # 6) Monta items
    items: list[dict] = []
    for l in leads:
        cartas = cotas_by_lead.get(l["id"], [])
        resumo = {
            "qtd_cartas": len(cartas),
            "qtd_ativas": len(cartas),  # aqui "ativo" = tem contrato no filtro
            "valor_total_cartas": None,
        }
        items.append(
            {
                "cliente": {
                    "lead_id": l["id"],
                    "nome": l.get("nome"),
                    "telefone": l.get("telefone"),
                    "email": l.get("email"),
                    "owner_user_id": l.get("owner_id"),
                },
                "cartas": [
                    {
                        "cota_id": c["id"],
                        "produto": c.get("produto"),
                        "numero_cota": c.get("numero_cota"),
                        "grupo_codigo": c.get("grupo_codigo"),
                        "valor_carta": c.get("valor_carta"),
                        "valor_parcela": c.get("valor_parcela"),
                        "prazo": c.get("prazo"),
                        "assembleia_dia": c.get("assembleia_dia"),
                    }
                    for c in sorted(cartas, key=lambda x: (x.get("created_at") or ""), reverse=True)
                ],
                "resumo": resumo,
            }
        )

    return {
        "page": page,
        "page_size": page_size,
        "items": items,
        "meta": {"include_all": include_all, "status": status},
    }