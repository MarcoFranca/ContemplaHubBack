# app/security/auth.py
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import Depends, Header, HTTPException, status
from supabase import Client

from app.core.supabase_safe import execute_with_retry
from app.deps import get_supabase_admin


ActorType = Literal["internal", "partner"]


@dataclass(frozen=True)
class CurrentProfile:
    user_id: str
    org_id: str
    role: str  # admin|gestor|vendedor|viewer

    @property
    def is_manager(self) -> bool:
        return self.role in ("admin", "gestor")


@dataclass(frozen=True)
class PartnerAccess:
    partner_user_id: str
    user_id: str
    org_id: str
    parceiro_id: str
    ativo: bool
    can_view_client_data: bool
    can_view_contracts: bool
    can_view_commissions: bool


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    org_id: str
    actor_type: ActorType
    role: str | None = None
    parceiro_id: str | None = None
    partner_user_id: str | None = None
    can_view_client_data: bool = False
    can_view_contracts: bool = False
    can_view_commissions: bool = False

    @property
    def is_internal(self) -> bool:
        return self.actor_type == "internal"

    @property
    def is_partner(self) -> bool:
        return self.actor_type == "partner"

    @property
    def is_manager(self) -> bool:
        return self.is_internal and (self.role in ("admin", "gestor"))


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )

    return parts[1]


def _get_authenticated_user_id(token: str, sb: Client) -> str:
    try:
        user = sb.auth.get_user(token).user
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    if not user or not user.id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return user.id


def get_current_profile(
    authorization: str | None = Header(default=None),
    sb: Client = Depends(get_supabase_admin),
) -> CurrentProfile:
    """
    Compatibilidade com o código atual:
    retorna apenas usuário interno (profiles).
    """
    token = _extract_bearer(authorization)
    user_id = _get_authenticated_user_id(token, sb)

    prof = execute_with_retry(
        sb.table("profiles")
        .select("user_id, org_id, role")
        .eq("user_id", user_id)
        .single()
    )

    data = getattr(prof, "data", None)
    if not data or not data.get("org_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário sem organização vinculada",
        )

    return CurrentProfile(
        user_id=data["user_id"],
        org_id=data["org_id"],
        role=(data.get("role") or "vendedor"),
    )


def get_current_partner(
    authorization: str | None = Header(default=None),
    sb: Client = Depends(get_supabase_admin),
) -> PartnerAccess:
    token = _extract_bearer(authorization)
    user_id = _get_authenticated_user_id(token, sb)

    partner = execute_with_retry(
        sb.table("partner_users")
        .select(
            """
            id,
            auth_user_id,
            org_id,
            parceiro_id,
            ativo,
            can_view_client_data,
            can_view_contracts,
            can_view_commissions
            """
        )
        .eq("auth_user_id", user_id)
        .eq("ativo", True)
        .single()
    )

    data = getattr(partner, "data", None)
    if not data or not data.get("org_id") or not data.get("parceiro_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Parceiro sem acesso válido",
        )

    try:
        execute_with_retry(
            sb.table("partner_users")
            .update({"last_login_at": datetime.utcnow().isoformat()})
            .eq("id", data["id"])
        )
    except Exception:
        pass

    return PartnerAccess(
        partner_user_id=data["id"],
        user_id=data["auth_user_id"],
        org_id=data["org_id"],
        parceiro_id=data["parceiro_id"],
        ativo=bool(data.get("ativo", False)),
        can_view_client_data=bool(data.get("can_view_client_data", False)),
        can_view_contracts=bool(data.get("can_view_contracts", False)),
        can_view_commissions=bool(data.get("can_view_commissions", False)),
    )


def get_auth_context(
    authorization: str | None = Header(default=None),
    sb: Client = Depends(get_supabase_admin),
) -> AuthContext:
    """
    Resolver unificado:
    - usuário interno (profiles)
    - parceiro (partner_users)

    Ordem:
    1. tenta profiles
    2. tenta partner_users
    """
    token = _extract_bearer(authorization)
    user_id = _get_authenticated_user_id(token, sb)

    prof = execute_with_retry(
        sb.table("profiles")
        .select("user_id, org_id, role")
        .eq("user_id", user_id)
        .maybe_single()
    )
    prof_data = getattr(prof, "data", None)

    if prof_data and prof_data.get("org_id"):
        return AuthContext(
            user_id=prof_data["user_id"],
            org_id=prof_data["org_id"],
            actor_type="internal",
            role=(prof_data.get("role") or "vendedor"),
            parceiro_id=None,
            partner_user_id=None,
            can_view_client_data=True,
            can_view_contracts=True,
            can_view_commissions=True,
        )

    partner = execute_with_retry(
        sb.table("partner_users")
        .select(
            """
            id,
            auth_user_id,
            org_id,
            parceiro_id,
            ativo,
            can_view_client_data,
            can_view_contracts,
            can_view_commissions
            """
        )
        .eq("auth_user_id", user_id)
        .eq("ativo", True)
        .maybe_single()
    )
    partner_data = getattr(partner, "data", None)

    if partner_data and partner_data.get("org_id") and partner_data.get("parceiro_id"):
        return AuthContext(
            user_id=partner_data["auth_user_id"],
            org_id=partner_data["org_id"],
            actor_type="partner",
            role=None,
            parceiro_id=partner_data["parceiro_id"],
            partner_user_id=partner_data["id"],
            can_view_client_data=bool(partner_data.get("can_view_client_data", False)),
            can_view_contracts=bool(partner_data.get("can_view_contracts", False)),
            can_view_commissions=bool(partner_data.get("can_view_commissions", False)),
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Usuário sem acesso válido ao sistema",
    )