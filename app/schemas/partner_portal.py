# app/schemas/partner_portal.py
from __future__ import annotations

from typing import Optional, Literal

from pydantic import BaseModel, Field


PartnerContractsSortBy = Literal[
    "created_at",
    "data_assinatura",
    "numero",
    "status",
]

PartnerCommissionsSortBy = Literal[
    "created_at",
    "competencia_prevista",
    "valor_bruto",
    "valor_liquido",
    "status",
    "repasse_status",
]

SortOrder = Literal["asc", "desc"]


class PartnerContractsQuery(BaseModel):
    status: Optional[str] = None
    q: Optional[str] = Field(
        default=None,
        description="Busca por número do contrato, número da cota, grupo ou cliente",
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_by: PartnerContractsSortBy = "created_at"
    sort_order: SortOrder = "desc"


class PartnerCommissionsQuery(BaseModel):
    status: Optional[str] = None
    repasse_status: Optional[str] = None
    contrato_id: Optional[str] = None
    competencia_de: Optional[str] = None
    competencia_ate: Optional[str] = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    sort_by: PartnerCommissionsSortBy = "competencia_prevista"
    sort_order: SortOrder = "desc"


class PartnerSignedUrlIn(BaseModel):
    expires_in: Optional[int] = Field(default=None, ge=60, le=3600)