from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.partner_users import (
    PartnerAccessToggleIn,
    PartnerUserInviteIn,
    PartnerUserResendInviteIn,
    PartnerUserUpdateIn,
)
from app.security.auth import AuthContext
from app.security.permissions import require_manager
from app.services.partner_users_service import (
    get_partner_user_detail,
    invite_partner_user,
    list_partner_users,
    resend_partner_invite,
    toggle_partner_user_access,
    update_partner_user,
)

router = APIRouter(prefix="/partner-users", tags=["partner-users"])


@router.get("")
def list_partner_accesses(
    ativos: Optional[bool] = Query(default=None),
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return list_partner_users(supa=supa, org_id=ctx.org_id, ativos=ativos)


@router.get("/{partner_user_id}")
def get_partner_access_detail(
    partner_user_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return get_partner_user_detail(
        supa=supa,
        org_id=ctx.org_id,
        partner_user_id=partner_user_id,
    )


@router.post("/invite")
def invite_partner_access(
    body: PartnerUserInviteIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return invite_partner_user(
        supa=supa,
        ctx=ctx,
        body=body,
    )


@router.post("/{partner_user_id}/resend-invite")
def resend_partner_access_invite(
    partner_user_id: str,
    body: PartnerUserResendInviteIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return resend_partner_invite(
        supa=supa,
        ctx=ctx,
        partner_user_id=partner_user_id,
        body=body,
    )


@router.patch("/{partner_user_id}")
def patch_partner_access(
    partner_user_id: str,
    body: PartnerUserUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return update_partner_user(
        supa=supa,
        ctx=ctx,
        partner_user_id=partner_user_id,
        body=body,
    )


@router.patch("/{partner_user_id}/toggle")
def toggle_partner_access(
    partner_user_id: str,
    body: PartnerAccessToggleIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return toggle_partner_user_access(
        supa=supa,
        ctx=ctx,
        partner_user_id=partner_user_id,
        body=body,
    )