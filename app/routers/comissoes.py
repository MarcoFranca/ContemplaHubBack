from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.comissoes import (
    ComissaoListFilters,
    CotaComissaoConfigUpsertIn,
    GerarLancamentosIn,
    LancamentoStatusUpdateIn,
    ParceiroCreateIn,
    ParceiroUpdateIn,
    RepasseUpdateIn,
)
from app.services.comissao_service import (
    cancel_comissao_for_cota,
    delete_comissao_for_cota,
    fetch_config_by_cota,
    fetch_lancamentos,
    fetch_parceiros_da_cota,
    fetch_regras,
    generate_lancamentos_for_contrato,
    get_delete_comissao_check,
    get_org_record_or_404,
    summarize_lancamentos,
    sync_eventos_contrato,
    upsert_config_for_cota,
)
router = APIRouter(prefix="/comissoes", tags=["comissoes"])


def require_org_id(x_org_id: Optional[str]) -> str:
    if not x_org_id:
        raise HTTPException(400, "X-Org-Id header é obrigatório")
    return x_org_id


@router.get("/parceiros")
def list_parceiros(
    ativos: Optional[bool] = None,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    query = supa.table("parceiros_corretores").select("*").eq("org_id", org_id)
    if ativos is not None:
        query = query.eq("ativo", ativos)
    resp = query.order("nome").execute()
    return {"ok": True, "items": getattr(resp, "data", None) or []}


@router.get("/cotas/{cota_id}/delete-check")
def check_delete_comissao_cota(
    cota_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    return get_delete_comissao_check(supa, org_id, cota_id)


@router.delete("/cotas/{cota_id}")
def delete_comissao_cota(
    cota_id: str,
    force: bool = Query(default=False),
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    return delete_comissao_for_cota(supa, org_id, cota_id, force=force)


@router.post("/cotas/{cota_id}/cancelar")
def cancelar_comissao_cota(
    cota_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    return cancel_comissao_for_cota(supa, org_id, cota_id)


@router.post("/parceiros")
def create_parceiro(
    body: ParceiroCreateIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    payload = {**body.model_dump(), "org_id": org_id}
    resp = supa.table("parceiros_corretores").insert(payload, returning="representation").execute()
    data = getattr(resp, "data", None) or []
    if not data:
        raise HTTPException(500, "Erro ao criar parceiro")
    return {"ok": True, "item": data[0]}


@router.patch("/parceiros/{parceiro_id}")
def update_parceiro(
    parceiro_id: str,
    body: ParceiroUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    get_org_record_or_404(supa, "parceiros_corretores", org_id, parceiro_id)
    payload = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    payload["updated_at"] = datetime.utcnow().isoformat()
    resp = (
        supa.table("parceiros_corretores")
        .update(payload)
        .eq("org_id", org_id)
        .eq("id", parceiro_id)
        .execute()
    )
    data = getattr(resp, "data", None) or []
    return {"ok": True, "item": data[0] if data else None}


@router.get("/parceiros/{parceiro_id}/extrato")
def parceiro_extrato(
    parceiro_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    parceiro = get_org_record_or_404(supa, "parceiros_corretores", org_id, parceiro_id)
    lancamentos = fetch_lancamentos(supa, org_id, parceiro_id=parceiro_id)
    return {
        "ok": True,
        "parceiro": parceiro,
        "items": lancamentos,
        "resumo": summarize_lancamentos(lancamentos),
    }


@router.get("/cotas/{cota_id}")
def get_config_cota(
    cota_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    config = fetch_config_by_cota(supa, org_id, cota_id)
    if not config:
        return {"ok": True, "config": None, "regras": [], "parceiros": []}
    regras = fetch_regras(supa, org_id, config["id"])
    parceiros = fetch_parceiros_da_cota(supa, org_id, cota_id)
    return {"ok": True, "config": config, "regras": regras, "parceiros": parceiros}


@router.put("/cotas/{cota_id}")
def put_config_cota(
    cota_id: str,
    body: CotaComissaoConfigUpsertIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    return upsert_config_for_cota(supa, org_id, cota_id, body)


@router.post("/contratos/{contrato_id}/gerar")
def gerar_lancamentos(
    contrato_id: str,
    body: GerarLancamentosIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    return generate_lancamentos_for_contrato(supa, org_id, contrato_id, sobrescrever=body.sobrescrever)


@router.post("/contratos/{contrato_id}/sincronizar-eventos")
def sincronizar_eventos(
    contrato_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    return sync_eventos_contrato(supa, org_id, contrato_id)


@router.get("/contratos/{contrato_id}")
def listar_por_contrato(
    contrato_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    items = fetch_lancamentos(supa, org_id, contrato_id=contrato_id)
    return {"ok": True, "items": items, "resumo": summarize_lancamentos(items)}


@router.get("/lancamentos")
def listar_lancamentos(
    parceiro_id: Optional[str] = Query(default=None),
    contrato_id: Optional[str] = Query(default=None),
    cota_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    repasse_status: Optional[str] = Query(default=None),
    competencia_de: Optional[str] = Query(default=None),
    competencia_ate: Optional[str] = Query(default=None),
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    filters = ComissaoListFilters(
        parceiro_id=parceiro_id,
        contrato_id=contrato_id,
        cota_id=cota_id,
        status=status,
        repasse_status=repasse_status,
        competencia_de=competencia_de,
        competencia_ate=competencia_ate,
    )
    items = fetch_lancamentos(supa, org_id, **filters.model_dump(exclude_none=True))
    return {"ok": True, "items": items, "resumo": summarize_lancamentos(items)}


@router.patch("/lancamentos/{lancamento_id}/status")
def atualizar_status_lancamento(
    lancamento_id: str,
    body: LancamentoStatusUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    lanc = get_org_record_or_404(supa, "comissao_lancamentos", org_id, lancamento_id)
    payload: Dict[str, Any] = {
        "status": body.status,
        "updated_at": datetime.utcnow().isoformat(),
    }
    if body.competencia_real:
        payload["competencia_real"] = body.competencia_real.isoformat()
    if body.status == "pago":
        payload["pago_em"] = datetime.utcnow().isoformat()
    if body.observacoes is not None:
        payload["observacoes"] = body.observacoes

    resp = (
        supa.table("comissao_lancamentos")
        .update(payload)
        .eq("id", lancamento_id)
        .eq("org_id", org_id)
        .execute()
    )
    data = getattr(resp, "data", None) or []
    return {"ok": True, "previous": lanc, "item": data[0] if data else None}


@router.patch("/lancamentos/{lancamento_id}/repasse")
def atualizar_repasse(
    lancamento_id: str,
    body: RepasseUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = require_org_id(x_org_id)
    lanc = get_org_record_or_404(supa, "comissao_lancamentos", org_id, lancamento_id)
    if lanc["beneficiario_tipo"] != "parceiro":
        raise HTTPException(400, "Repasse só se aplica a lançamentos de parceiro")

    payload: Dict[str, Any] = {
        "repasse_status": body.repasse_status,
        "repasse_previsto_em": body.repasse_previsto_em.isoformat() if body.repasse_previsto_em else None,
        "repasse_observacoes": body.repasse_observacoes,
        "updated_at": datetime.utcnow().isoformat(),
    }
    if body.repasse_status == "pago":
        payload["repasse_pago_em"] = (body.repasse_pago_em or datetime.utcnow()).isoformat()
    elif body.repasse_status != "pago":
        payload["repasse_pago_em"] = None

    resp = (
        supa.table("comissao_lancamentos")
        .update(payload)
        .eq("org_id", org_id)
        .eq("id", lancamento_id)
        .execute()
    )
    data = getattr(resp, "data", None) or []
    return {"ok": True, "previous": lanc, "item": data[0] if data else None}
