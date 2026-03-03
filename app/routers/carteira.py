# app/routers/carteira.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from supabase import Client

from app.deps import get_supabase_admin
from app.security.auth import CurrentProfile, get_current_profile

router = APIRouter(prefix="/v1/carteira", tags=["carteira"])

def _apply_situacao_filter(q, include_all: bool, situacao: str | None):
    if situacao:
        return q.eq("situacao", situacao)
    if not include_all:
        return q.eq("situacao", "ativa")
    return q  # sem filtro

def _normalize_q(q: str | None) -> str | None:
    if not q:
        return None
    qq = q.strip()
    return qq if qq else None

@router.get("/cartas")
def list_carteira_cartas(
    include_all: bool = Query(default=False),
    situacao: str | None = Query(default=None, description="Se informado, ignora include_all"),
    produto: str | None = Query(default=None),
    owner_user_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Busca por nome/telefone/email"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    sb: Client = Depends(get_supabase_admin),
    me: CurrentProfile = Depends(get_current_profile),
):
    """
    Retorna visão flat (linha por cota).
    Default: somente cotas ativas.
    """
    qq = _normalize_q(q)
    offset = (page - 1) * page_size

    # 1) CotAs (filtra por org e situacao)
    qc = (
        sb.table("cotas")
        .select(
            "id, lead_id, situacao, produto, numero_cota, grupo_codigo, valor_carta, valor_parcela, prazo, assembleia_dia, "
            "embutido_permitido, embutido_max_percent, fgts_permitido, tipo_lance_preferencial, created_at"
        )
        .eq("org_id", me.org_id)
    )

    qc = _apply_situacao_filter(qc, include_all, situacao)

    if produto:
        qc = qc.eq("produto", produto)

    # paginação (PostgREST)
    qc = qc.order("created_at", desc=True).range(offset, offset + page_size - 1)

    cotas_res = qc.execute()
    cotas = cotas_res.data or []

    lead_ids = list({c["lead_id"] for c in cotas if c.get("lead_id")})
    leads_map: dict[str, dict] = {}

    if lead_ids:
        ql = (
            sb.table("leads")
            .select("id, nome, telefone, email, owner_id")
            .in_("id", lead_ids)
            .eq("org_id", me.org_id)
        )

        # regra de permissão: vendedor só vê leads dele
        if not me.is_manager:
            ql = ql.eq("owner_id", me.user_id)

        # filtro por owner explícito (só faz sentido para gestor/admin)
        if owner_user_id and me.is_manager:
            ql = ql.eq("owner_id", owner_user_id)

        # busca
        if qq:
            # supabase-py não tem OR elegante universal; fazemos 3 queries e unimos ids.
            # Para MVP, aplica busca apenas por nome (mais comum).
            ql = ql.ilike("nome", f"%{qq}%")

        leads_res = ql.execute()
        for l in (leads_res.data or []):
            leads_map[l["id"]] = l

    # filtra cotas sem lead visível (quando vendedor)
    items = []
    for c in cotas:
        lead = leads_map.get(c.get("lead_id"))
        if not lead:
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
                    "cota_id": c["id"],
                    "situacao": c.get("situacao"),
                    "produto": c.get("produto"),
                    "numero_cota": c.get("numero_cota"),
                    "grupo_codigo": c.get("grupo_codigo"),
                    "valor_carta": c.get("valor_carta"),
                    "valor_parcela": c.get("valor_parcela"),
                    "prazo": c.get("prazo"),
                    "assembleia_dia": c.get("assembleia_dia"),
                    "embutido_permitido": c.get("embutido_permitido"),
                    "embutido_max_percent": c.get("embutido_max_percent"),
                    "fgts_permitido": c.get("fgts_permitido"),
                    "tipo_lance_preferencial": c.get("tipo_lance_preferencial"),
                },
            }
        )

    return {
        "page": page,
        "page_size": page_size,
        "items": items,
        # total: para total exato, ideal é usar count=exact (depende da lib).
        "meta": {"include_all": include_all, "situacao": situacao},
    }


@router.get("/clientes")
def list_carteira_clientes(
    include_all: bool = Query(default=False),
    situacao: str | None = Query(default=None, description="Se informado, ignora include_all"),
    only_deal_ganho: bool = Query(default=True),
    owner_user_id: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Busca por nome/telefone/email (MVP: nome)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    sb: Client = Depends(get_supabase_admin),
    me: CurrentProfile = Depends(get_current_profile),
):
    """
    Retorna visão agrupada: cliente + cartas[].
    Default: somente cotas ativas.
    Default: only_deal_ganho=true.
    """
    qq = _normalize_q(q)
    offset = (page - 1) * page_size

    # 1) universo de leads (deal ganho)
    deal_lead_ids: list[str] | None = None
    if only_deal_ganho:
        dq = (
            sb.table("deals")
            .select("lead_id")
            .eq("org_id", me.org_id)
            .eq("status", "ganho")
        )
        # vendedor só vê seus leads (via join no leads seria melhor; MVP: filtra depois)
        deals_res = dq.execute()
        deal_lead_ids = list({d["lead_id"] for d in (deals_res.data or []) if d.get("lead_id")})
        if not deal_lead_ids:
            return {"page": page, "page_size": page_size, "items": [], "meta": {"only_deal_ganho": True}}

    # 2) paginação de leads
    ql = (
        sb.table("leads")
        .select("id, nome, telefone, email, owner_id")
        .eq("org_id", me.org_id)
    )

    if deal_lead_ids is not None:
        ql = ql.in_("id", deal_lead_ids)

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

    # 3) cotas desses leads (com filtro ativa/todas)
    qc = (
        sb.table("cotas")
        .select(
            "id, lead_id, situacao, produto, numero_cota, grupo_codigo, valor_carta, valor_parcela, prazo, assembleia_dia, created_at"
        )
        .eq("org_id", me.org_id)
        .in_("lead_id", lead_ids)
    )

    qc = _apply_situacao_filter(qc, include_all, situacao)
    qc = qc.order("created_at", desc=True)
    cotas_res = qc.execute()
    cotas = cotas_res.data or []

    # 4) agrupa
    cotas_by_lead: dict[str, list[dict]] = {}
    for c in cotas:
        cotas_by_lead.setdefault(c["lead_id"], []).append(c)

    items = []
    for l in leads:
        cartas = cotas_by_lead.get(l["id"], [])
        # se default é "ativas", cliente pode ficar sem cartas (ex.: só contempladas) – decide se exibe ou não.
        # eu recomendo exibir, mas com cartas=[] (e o front mostra "sem cotas ativas").
        resumo = {
            "qtd_cartas": len(cartas),
            "qtd_ativas": sum(1 for c in cartas if (c.get("situacao") == "ativa")),
            "valor_total_cartas": None,  # opcional somar no front (numeric vem string)
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
                        "situacao": c.get("situacao"),
                        "produto": c.get("produto"),
                        "numero_cota": c.get("numero_cota"),
                        "grupo_codigo": c.get("grupo_codigo"),
                        "valor_carta": c.get("valor_carta"),
                        "valor_parcela": c.get("valor_parcela"),
                        "prazo": c.get("prazo"),
                        "assembleia_dia": c.get("assembleia_dia"),
                    }
                    for c in cartas
                ],
                "resumo": resumo,
            }
        )

    return {
        "page": page,
        "page_size": page_size,
        "items": items,
        "meta": {"include_all": include_all, "situacao": situacao, "only_deal_ganho": only_deal_ganho},
    }