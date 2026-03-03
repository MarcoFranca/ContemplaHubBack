# app/security/auth.py
from dataclasses import dataclass
from fastapi import Depends, Header, HTTPException
from supabase import Client

from app.deps import get_supabase_admin

@dataclass(frozen=True)
class CurrentProfile:
    user_id: str
    org_id: str
    role: str  # admin|gestor|vendedor|viewer

    @property
    def is_manager(self) -> bool:
        return self.role in ("admin", "gestor")

def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    return parts[1]

def get_current_profile(
    authorization: str | None = Header(default=None),
    sb: Client = Depends(get_supabase_admin),
) -> CurrentProfile:
    token = _extract_bearer(authorization)

    # 1) valida token e pega user
    try:
        user = sb.auth.get_user(token).user
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not user or not user.id:
        raise HTTPException(status_code=401, detail="Invalid token")

    # 2) carrega profile (org_id/role)
    prof = (
        sb.table("profiles")
        .select("user_id, org_id, role")
        .eq("user_id", user.id)
        .single()
        .execute()
    )

    data = getattr(prof, "data", None)
    if not data or not data.get("org_id"):
        raise HTTPException(status_code=403, detail="Usuário sem organização vinculada")

    return CurrentProfile(
        user_id=data["user_id"],
        org_id=data["org_id"],
        role=(data.get("role") or "vendedor"),
    )