from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class PartnerUserInviteIn(BaseModel):
    parceiro_id: str
    email: EmailStr
    nome: Optional[str] = Field(default=None, min_length=2)
    telefone: Optional[str] = None

    can_view_client_data: bool = False
    can_view_contracts: bool = True
    can_view_commissions: bool = True
    ativo: bool = True


class PartnerUserUpdateIn(BaseModel):
    nome: Optional[str] = Field(default=None, min_length=2)
    telefone: Optional[str] = None
    ativo: Optional[bool] = None
    disabled_reason: Optional[str] = None

    can_view_client_data: Optional[bool] = None
    can_view_contracts: Optional[bool] = None
    can_view_commissions: Optional[bool] = None


class PartnerUserResendInviteIn(BaseModel):
    redirect_to: Optional[str] = None


class PartnerAccessToggleIn(BaseModel):
    ativo: bool
    disabled_reason: Optional[str] = None