from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.lances import (
    AtualizarCartaPayload,
    AtualizarResultadoLancePayload,
    CancelarCotaPayload,
    ContemplarCotaPayload,
    ControleMensalPayload,
    LanceCartaListResponse,
    LancesCartaDetalheOut,
    RegistrarLancePayload,
    SimpleOkResponse,
)

from app.security.auth import CurrentProfile, get_current_profile
from app.services.lances_service import (
    atualizar_carta,
    cancelar_cota,
    contemplar_cota,
    get_carta_detalhe,
    list_cartas_operacao,
    list_regras_operadora,
    normalize_competencia,
    reativar_cota,
    registrar_lance,
    upsert_controle_mensal,
)

router = APIRouter(prefix="/lances", tags=["lances"])


@router.patch("/cartas/{cota_id}", response_model=SimpleOkResponse)
def patch_atualizar_carta(
    cota_id: str,
    payload: AtualizarCartaPayload,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    atualizar_carta(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        payload=payload,
    )
    return {"ok": True}


@router.get("/cartas", response_model=LanceCartaListResponse)
def get_cartas_lance(
    competencia: date = Query(...),
    status_cota: str = Query(default="ativa"),
    administradora_id: str | None = Query(default=None),
    produto: str | None = Query(default=None),
    somente_autorizadas: bool = Query(default=False),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    return list_cartas_operacao(
        sb=sb,
        profile=profile,
        competencia=competencia,
        status_cota=status_cota,
        administradora_id=administradora_id,
        produto=produto,
        somente_autorizadas=somente_autorizadas,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.get("/cartas/{cota_id}", response_model=LancesCartaDetalheOut)
def get_detalhe_carta(
    cota_id: str,
    competencia: date = Query(...),
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    return get_carta_detalhe(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        competencia=competencia,
    )


@router.patch("/cartas/{cota_id}", response_model=SimpleOkResponse)
def patch_atualizar_carta(
    cota_id: str,
    payload: AtualizarCartaPayload,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    atualizar_carta(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        payload=payload,
    )
    return {"ok": True}


@router.post("/cartas/{cota_id}/controle-mensal", response_model=SimpleOkResponse)
def post_controle_mensal(
    cota_id: str,
    payload: ControleMensalPayload,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    upsert_controle_mensal(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        competencia=payload.competencia,
        status_mes=payload.status_mes,
        observacoes=payload.observacoes,
    )
    return {"ok": True}


@router.post("/cartas/{cota_id}/registrar-lance")
def post_registrar_lance(
    cota_id: str,
    payload: RegistrarLancePayload,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    return registrar_lance(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        competencia=payload.competencia,
        assembleia_data=payload.assembleia_data,
        tipo=payload.tipo,
        percentual=payload.percentual,
        valor=payload.valor,
        base_calculo=payload.base_calculo,
        pagamento=payload.pagamento,
        resultado=payload.resultado,
        observacoes_competencia=payload.observacoes_competencia,
        cota_lance_fixo_opcao_id=str(payload.cota_lance_fixo_opcao_id) if payload.cota_lance_fixo_opcao_id else None,
    )


@router.patch("/{lance_id}/resultado", response_model=SimpleOkResponse)
def patch_resultado_lance(
    lance_id: str,
    payload: AtualizarResultadoLancePayload,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    resp = (
        sb.table("lances")
        .update({"resultado": payload.resultado})
        .eq("org_id", profile.org_id)
        .eq("id", lance_id)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(404, "Lance não encontrado")
    return {"ok": True}


@router.post("/cartas/{cota_id}/contemplar", response_model=SimpleOkResponse)
def post_contemplar_cota(
    cota_id: str,
    payload: ContemplarCotaPayload,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    contemplar_cota(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        data=payload.data,
        motivo=payload.motivo,
        lance_percentual=payload.lance_percentual,
        competencia=payload.competencia,
    )
    return {"ok": True}


@router.post("/cartas/{cota_id}/cancelar", response_model=SimpleOkResponse)
def post_cancelar_cota(
    cota_id: str,
    payload: CancelarCotaPayload,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    cancelar_cota(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        competencia=payload.competencia,
        observacoes=payload.observacoes,
    )
    return {"ok": True}


@router.post("/cartas/{cota_id}/reativar", response_model=SimpleOkResponse)
def post_reativar_cota(
    cota_id: str,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    reativar_cota(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
    )
    return {"ok": True}


@router.get("/config/regras-operadora")
def get_regras_operadora(
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    return list_regras_operadora(sb=sb, profile=profile)