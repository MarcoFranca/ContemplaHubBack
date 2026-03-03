# app/routers/carteira.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from supabase import Client

from app.deps import get_supabase_admin
from app.security.auth import CurrentProfile, get_current_profile

router = APIRouter(prefix="/v1/carteira", tags=["carteira"])


def _normalize_q(q: str | None) -> str | None:
    if not q:
        return None
    qq = q.strip()
    return qq if qq else None


def _apply_status_filter_contratos(qc, include_all: bool, contrato_status: str | None):
    """
    Default: só ativos (contratos.status='ativo')
    Se contrato_status vier informado, ignora include_all.
    """
    if contrato_status:
        return qc.eq("status", contrato_status)
    if not include_all:
        return qc.eq("status", "ativo")
    return qc  # sem filtro


@router.get("/cartas")
def list_carteira_cartas(
    include_all: bool = Query(default=False, description="Se false, filtra contratos.status='ativo'"),
    contrato_status: str | None = Query(default=None, description="Se informado, ignora include_all"),
    produto: str | None = Query(default=None, description="Filtra cotas.produto (ex: imobiliario/auto)"),
    owner_user_id: str | None = Query(default=None, description="Filtra leads.owner_id (somente para manager/admin)"),
    q: str | None = Query(default=None, description="Busca por nome (MVP)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    sb: Client = Depends(get_supabase_admin),
    me: CurrentProfile = Depends(get_current_profile),
):
    """
    Visão FLAT (1 linha por cota/contrato): cliente + cota + contrato (+deal opcional).
    Default: deals.status='ganho' e contratos.status='ativo'.
    """
    qq = _normalize_q(q)
    offset = (page - 1) * page_size

    # 1) Deals ganhos da org (universo da carteira)
    dq = (
        sb.table("deals")
        .select("id, lead_id, owner_id, status, closed_at, created_at, valor_carta, prazo_meses, administradora")
        .eq("org_id", me.org_id)
        .eq("status", "ganho")
    )

    # permissão: vendedor só vê os leads dele (owner_id no deal pode não existir no seu schema;
    # no seu schema 'deals' tem createdBy, mas não owner_id. Então o controle real será via leads.owner_id.)
    deals_res = dq.execute()
    deals = deals_res.data or []
    if not deals:
        return {"page": page, "page_size": page_size, "items": [], "meta": {"only_deal_ganho": True}}

    deal_ids = [d["id"] for d in deals if d.get("id")]
    deal_by_id = {d["id"]: d for d in deals if d.get("id")}

    # 2) Contratos desses deals (aqui fica o status "ativo")
    qc = (
        sb.table("contratos")
        .select("id, deal_id, cota_id, numero, status, data_assinatura, data_pagamento, data_alocacao, data_contemplacao, created_at")
        .eq("org_id", me.org_id)
        .in_("deal_id", deal_ids)
    )
    qc = _apply_status_filter_contratos(qc, include_all, contrato_status)
    qc = qc.order("created_at", desc=True).range(offset, offset + page_size - 1)

    contratos_res = qc.execute()
    contratos = contratos_res.data or []
    if not contratos:
        return {
            "page": page,
            "page_size": page_size,
            "items": [],
            "meta": {"include_all": include_all, "contrato_status": contrato_status, "only_deal_ganho": True},
        }

    # 3) Cotas por cota_id
    cota_ids = list({c["cota_id"] for c in contratos if c.get("cota_id")})
    cotas_map: dict[str, dict] = {}
    if cota_ids:
        qcot = (
            sb.table("cotas")
            .select(
                "id, org_id, lead_id, administradora_id, produto, numero_cota, grupo_codigo, valor_carta, valor_parcela, prazo, "
                "assembleia_dia, embutido_permitido, embutido_max_percent, fgts_permitido, tipo_lance_preferencial, created_at"
            )
            .eq("org_id", me.org_id)
            .in_("id", cota_ids)
        )
        if produto:
            qcot = qcot.eq("produto", produto)

        cotas_res = qcot.execute()
        for c in (cotas_res.data or []):
            cotas_map[c["id"]] = c

    # 4) Leads por lead_id (vindo do deal e/ou cota)
    lead_ids = set()
    for d in deals:
        if d.get("lead_id"):
            lead_ids.add(d["lead_id"])
    for cota in cotas_map.values():
        if cota.get("lead_id"):
            lead_ids.add(cota["lead_id"])

    leads_map: dict[str, dict] = {}
    if lead_ids:
        ql = (
            sb.table("leads")
            .select("id, nome, telefone, email, owner_id")
            .eq("org_id", me.org_id)
            .in_("id", list(lead_ids))
        )

        # permissão: vendedor só vê leads dele
        if not me.is_manager:
            ql = ql.eq("owner_id", me.user_id)

        # filtro por owner explícito (para manager/admin)
        if owner_user_id and me.is_manager:
            ql = ql.eq("owner_id", owner_user_id)

        # busca MVP: nome
        if qq:
            ql = ql.ilike("nome", f"%{qq}%")

        leads_res = ql.execute()
        for l in (leads_res.data or []):
            leads_map[l["id"]] = l

    # 5) Monta items (filtra o que não pode ver)
    items = []
    for ct in contratos:
        deal = deal_by_id.get(ct.get("deal_id"))
        lead_id = None
        if deal and deal.get("lead_id"):
            lead_id = deal["lead_id"]
        else:
            # fallback: tenta pela cota
            cota = cotas_map.get(ct.get("cota_id"))
            lead_id = cota.get("lead_id") if cota else None

        lead = leads_map.get(lead_id) if lead_id else None
        if not lead:
            continue

        cota = cotas_map.get(ct.get("cota_id"))
        if produto and (not cota or cota.get("produto") != produto):
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
                "cota": (
                    {
                        "cota_id": cota.get("id"),
                        "produto": cota.get("produto"),
                        "numero_cota": cota.get("numero_cota"),
                        "grupo_codigo": cota.get("grupo_codigo"),
                        "valor_carta": cota.get("valor_carta"),
                        "valor_parcela": cota.get("valor_parcela"),
                        "prazo": cota.get("prazo"),
                        "assembleia_dia": cota.get("assembleia_dia"),
                        "embutido_permitido": cota.get("embutido_permitido"),
                        "embutido_max_percent": cota.get("embutido_max_percent"),
                        "fgts_permitido": cota.get("fgts_permitido"),
                        "tipo_lance_preferencial": cota.get("tipo_lance_preferencial"),
                    }
                    if cota
                    else None
                ),
                "contrato": {
                    "contrato_id": ct.get("id"),
                    "deal_id": ct.get("deal_id"),
                    "cota_id": ct.get("cota_id"),
                    "numero": ct.get("numero"),
                    "status": ct.get("status"),
                    "data_assinatura": ct.get("data_assinatura"),
                    "data_pagamento": ct.get("data_pagamento"),
                    "data_alocacao": ct.get("data_alocacao"),
                    "data_contemplacao": ct.get("data_contemplacao"),
                    "created_at": ct.get("created_at"),
                },
                "deal": {
                    "deal_id": deal.get("id") if deal else None,
                    "status": deal.get("status") if deal else None,
                    "closed_at": deal.get("closed_at") if deal else None,
                    "valor_carta": deal.get("valor_carta") if deal else None,
                    "prazo_meses": deal.get("prazo_meses") if deal else None,
                    "administradora": deal.get("administradora") if deal else None,
                },
            }
        )

    return {
        "page": page,
        "page_size": page_size,
        "items": items,
        "meta": {
            "include_all": include_all,
            "contrato_status": contrato_status,
            "produto": produto,
            "only_deal_ganho": True,
        },
    }


@router.get("/clientes")
def list_carteira_clientes(
    include_all: bool = Query(default=False, description="Se false, filtra contratos.status='ativo'"),
    contrato_status: str | None = Query(default=None, description="Se informado, ignora include_all"),
    produto: str | None = Query(default=None),
    only_deal_ganho: bool = Query(default=True),
    owner_user_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Busca por nome (MVP)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    sb: Client = Depends(get_supabase_admin),
    me: CurrentProfile = Depends(get_current_profile),
):
    """
    Visão AGRUPADA: cliente + cartas[] (cartas derivadas de contratos + cotas).
    Default: only_deal_ganho=true e contratos.status='ativo'
    """
    qq = _normalize_q(q)
    offset = (page - 1) * page_size

    # 1) Deals (universo carteira)
    dq = sb.table("deals").select("id, lead_id, status").eq("org_id", me.org_id)
    if only_deal_ganho:
        dq = dq.eq("status", "ganho")

    deals_res = dq.execute()
    deals = deals_res.data or []
    if not deals:
        return {"page": page, "page_size": page_size, "items": [], "meta": {"only_deal_ganho": only_deal_ganho}}

    deal_ids = [d["id"] for d in deals if d.get("id")]
    lead_ids_from_deals = list({d["lead_id"] for d in deals if d.get("lead_id")})

    if not lead_ids_from_deals:
        return {"page": page, "page_size": page_size, "items": [], "meta": {"only_deal_ganho": only_deal_ganho}}

    # 2) Leads paginados
    ql = (
        sb.table("leads")
        .select("id, nome, telefone, email, owner_id")
        .eq("org_id", me.org_id)
        .in_("id", lead_ids_from_deals)
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
    lead_ids = [l["id"] for l in leads]
    if not lead_ids:
        return {"page": page, "page_size": page_size, "items": [], "meta": {"only_deal_ganho": only_deal_ganho}}

    # 3) Contratos dos deals desses leads
    # (primeiro resolve deals desses leads)
    deals_by_lead: dict[str, list[str]] = {}
    for d in deals:
        if d.get("lead_id") in lead_ids and d.get("id"):
            deals_by_lead.setdefault(d["lead_id"], []).append(d["id"])

    lead_deal_ids = list({did for ids in deals_by_lead.values() for did in ids})
    if not lead_deal_ids:
        return {"page": page, "page_size": page_size, "items": [], "meta": {"only_deal_ganho": only_deal_ganho}}

    qc = (
        sb.table("contratos")
        .select("id, deal_id, cota_id, numero, status, data_assinatura, data_pagamento, data_alocacao, data_contemplacao, created_at")
        .eq("org_id", me.org_id)
        .in_("deal_id", lead_deal_ids)
    )
    qc = _apply_status_filter_contratos(qc, include_all, contrato_status)
    qc = qc.order("created_at", desc=True)

    contratos_res = qc.execute()
    contratos = contratos_res.data or []

    # 4) Cotas desses contratos
    cota_ids = list({c["cota_id"] for c in contratos if c.get("cota_id")})
    cotas_map: dict[str, dict] = {}
    if cota_ids:
        qcot = (
            sb.table("cotas")
            .select(
                "id, lead_id, produto, numero_cota, grupo_codigo, valor_carta, valor_parcela, prazo, assembleia_dia, created_at"
            )
            .eq("org_id", me.org_id)
            .in_("id", cota_ids)
        )
        if produto:
            qcot = qcot.eq("produto", produto)

        cotas_res = qcot.execute()
        for c in (cotas_res.data or []):
            cotas_map[c["id"]] = c

    # 5) Agrupar por lead
    contratos_by_lead: dict[str, list[dict]] = {lid: [] for lid in lead_ids}

    # map deal_id -> lead_id
    deal_to_lead: dict[str, str] = {}
    for d in deals:
        if d.get("id") and d.get("lead_id"):
            deal_to_lead[d["id"]] = d["lead_id"]

    for ct in contratos:
        lead_id = deal_to_lead.get(ct.get("deal_id"))
        if not lead_id or lead_id not in contratos_by_lead:
            continue
        cota = cotas_map.get(ct.get("cota_id"))
        if produto and (not cota or cota.get("produto") != produto):
            continue

        contratos_by_lead[lead_id].append(
            {
                "contrato_id": ct.get("id"),
                "deal_id": ct.get("deal_id"),
                "status": ct.get("status"),
                "numero": ct.get("numero"),
                "data_assinatura": ct.get("data_assinatura"),
                "data_pagamento": ct.get("data_pagamento"),
                "data_alocacao": ct.get("data_alocacao"),
                "data_contemplacao": ct.get("data_contemplacao"),
                "created_at": ct.get("created_at"),
                "cota": (
                    {
                        "cota_id": cota.get("id"),
                        "produto": cota.get("produto"),
                        "numero_cota": cota.get("numero_cota"),
                        "grupo_codigo": cota.get("grupo_codigo"),
                        "valor_carta": cota.get("valor_carta"),
                        "valor_parcela": cota.get("valor_parcela"),
                        "prazo": cota.get("prazo"),
                        "assembleia_dia": cota.get("assembleia_dia"),
                        "created_at": cota.get("created_at"),
                    }
                    if cota
                    else None
                ),
            }
        )

    items = []
    for l in leads:
        cartas = contratos_by_lead.get(l["id"], [])

        resumo = {
            "qtd_cartas": len(cartas),
            "qtd_contratos_ativos": sum(1 for c in cartas if c.get("status") == "ativo"),
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
                "cartas": cartas,
                "resumo": resumo,
            }
        )

    return {
        "page": page,
        "page_size": page_size,
        "items": items,
        "meta": {
            "include_all": include_all,
            "contrato_status": contrato_status,
            "produto": produto,
            "only_deal_ganho": only_deal_ganho,
        },
    }

