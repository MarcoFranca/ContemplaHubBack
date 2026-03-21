# app/routers/partner_portal.py
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.partner_portal import PartnerSignedUrlIn
from app.security.auth import AuthContext
from app.security.permissions import require_partner_user
from app.services.partner_portal_service import (
    create_partner_contract_signed_url,
    get_partner_contract_detail,
    get_partner_user_me,
    list_partner_commissions,
    list_partner_contracts,
)

router = APIRouter(prefix="/partner", tags=["partner-portal"])


@router.get("/me")
def partner_me(
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_partner_user),
):
    return get_partner_user_me(supa, ctx=ctx)


@router.get("/contracts")
def partner_contracts(
    status: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_partner_user),
):
    return list_partner_contracts(
        supa,
        ctx=ctx,
        status=status,
        q=q,
        limit=limit,
    )


@router.get("/contracts/{contract_id}")
def partner_contract_detail(
    contract_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_partner_user),
):
    return get_partner_contract_detail(
        supa,
        ctx=ctx,
        contract_id=contract_id,
    )


@router.get("/commissions")
def partner_commissions(
    status: Optional[str] = Query(default=None),
    repasse_status: Optional[str] = Query(default=None),
    contrato_id: Optional[str] = Query(default=None),
    competencia_de: Optional[str] = Query(default=None),
    competencia_ate: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_partner_user),
):
    return list_partner_commissions(
        supa,
        ctx=ctx,
        status=status,
        repasse_status=repasse_status,
        contrato_id=contrato_id,
        competencia_de=competencia_de,
        competencia_ate=competencia_ate,
        limit=limit,
    )


@router.post("/contracts/{contract_id}/document/signed-url")
def partner_contract_signed_url(
    contract_id: str,
    body: PartnerSignedUrlIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_partner_user),
):
    return create_partner_contract_signed_url(
        supa,
        ctx=ctx,
        contract_id=contract_id,
        expires_in=body.expires_in,
    )