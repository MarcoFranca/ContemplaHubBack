from fastapi import Depends, HTTPException, status

from app.core.auth_context import AuthContext, get_auth_context


def require_internal_user(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if ctx.actor_type != "internal":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso permitido apenas para usuários internos",
        )
    return ctx


def require_partner_user(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if ctx.actor_type != "partner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso permitido apenas para parceiros",
        )
    return ctx


def require_same_org(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """
    Placeholder para validações futuras mais complexas
    """
    return ctx