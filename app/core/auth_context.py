from typing import Optional, Literal
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.supabase_client import get_supabase_admin
from app.core.config import settings

security = HTTPBearer()


ActorType = Literal["internal", "partner"]


@dataclass
class AuthContext:
    user_id: str
    org_id: str
    actor_type: ActorType
    partner_id: Optional[str] = None


async def get_auth_context(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> AuthContext:
    """
    Resolve se o usuário é:
    - interno (profiles)
    - parceiro (partner_users)

    SEMPRE retorna org_id validado
    """

    token = credentials.credentials
    supabase = get_supabase_admin()

    # 🔐 valida token
    user = supabase.auth.get_user(token)
    if not user or not user.user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )

    user_id = user.user.id

    # --------------------------------------------------
    # 1. Tenta resolver como usuário interno (profiles)
    # --------------------------------------------------
    profile = (
        supabase.table("profiles")
        .select("id, org_id, role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )

    if profile.data:
        return AuthContext(
            user_id=user_id,
            org_id=profile.data["org_id"],
            actor_type="internal",
        )

    # --------------------------------------------------
    # 2. Tenta resolver como parceiro
    # --------------------------------------------------
    partner = (
        supabase.table("partner_users")
        .select("id, org_id, partner_id, status")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )

    if partner.data:
        if partner.data["status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Parceiro inativo",
            )

        return AuthContext(
            user_id=user_id,
            org_id=partner.data["org_id"],
            actor_type="partner",
            partner_id=partner.data["partner_id"],
        )

    # --------------------------------------------------
    # fallback inválido
    # --------------------------------------------------
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Usuário não autorizado neste sistema",
    )