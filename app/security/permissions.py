# app/security/permissions.py
from fastapi import Depends, HTTPException, status

from app.security.auth import AuthContext, get_auth_context


def require_auth_context(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    return ctx


def require_internal_user(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    if not ctx.is_internal:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso permitido apenas para usuários internos",
        )
    return ctx


def require_partner_user(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    if not ctx.is_partner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso permitido apenas para parceiros",
        )
    return ctx


def require_manager(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    if not ctx.is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso permitido apenas para admin ou gestor",
        )
    return ctx


def require_partner_contract_access(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    if not ctx.is_partner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso permitido apenas para parceiros",
        )

    if not ctx.can_view_contracts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Parceiro sem permissão para visualizar contratos",
        )

    return ctx


def require_partner_commission_access(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    if not ctx.is_partner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso permitido apenas para parceiros",
        )

    if not ctx.can_view_commissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Parceiro sem permissão para visualizar comissões",
        )

    return ctx