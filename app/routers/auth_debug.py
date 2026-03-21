# app/routers/auth_debug.py
from fastapi import APIRouter, Depends

from app.security.auth import (
    AuthContext,
    CurrentProfile,
    PartnerAccess,
    get_auth_context,
    get_current_partner,
    get_current_profile,
)
from app.security.permissions import (
    require_internal_user,
    require_manager,
    require_partner_user,
)

router = APIRouter(prefix="/auth-debug", tags=["auth-debug"])


@router.get("/context")
def auth_context_debug(
    ctx: AuthContext = Depends(get_auth_context),
):
    return {
        "user_id": ctx.user_id,
        "org_id": ctx.org_id,
        "actor_type": ctx.actor_type,
        "role": ctx.role,
        "parceiro_id": ctx.parceiro_id,
        "partner_user_id": ctx.partner_user_id,
        "can_view_client_data": ctx.can_view_client_data,
        "can_view_contracts": ctx.can_view_contracts,
        "can_view_commissions": ctx.can_view_commissions,
        "is_internal": ctx.is_internal,
        "is_partner": ctx.is_partner,
        "is_manager": ctx.is_manager,
    }


@router.get("/internal")
def internal_debug(
    profile: CurrentProfile = Depends(get_current_profile),
):
    return {
        "user_id": profile.user_id,
        "org_id": profile.org_id,
        "role": profile.role,
        "is_manager": profile.is_manager,
    }


@router.get("/partner")
def partner_debug(
    partner: PartnerAccess = Depends(get_current_partner),
):
    return {
        "partner_user_id": partner.partner_user_id,
        "user_id": partner.user_id,
        "org_id": partner.org_id,
        "parceiro_id": partner.parceiro_id,
        "ativo": partner.ativo,
        "can_view_client_data": partner.can_view_client_data,
        "can_view_contracts": partner.can_view_contracts,
        "can_view_commissions": partner.can_view_commissions,
    }


@router.get("/manager-only")
def manager_only_debug(
    ctx: AuthContext = Depends(require_manager),
):
    return {
        "ok": True,
        "message": "Acesso de gestor/admin validado",
        "user_id": ctx.user_id,
        "org_id": ctx.org_id,
        "role": ctx.role,
    }


@router.get("/partner-only")
def partner_only_debug(
    ctx: AuthContext = Depends(require_partner_user),
):
    return {
        "ok": True,
        "message": "Acesso de parceiro validado",
        "user_id": ctx.user_id,
        "org_id": ctx.org_id,
        "parceiro_id": ctx.parceiro_id,
    }


@router.get("/internal-only")
def internal_only_debug(
    ctx: AuthContext = Depends(require_internal_user),
):
    return {
        "ok": True,
        "message": "Acesso interno validado",
        "user_id": ctx.user_id,
        "org_id": ctx.org_id,
        "role": ctx.role,
    }