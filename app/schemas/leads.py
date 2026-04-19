from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator, model_validator

from app.schemas.kanban import Stage


class LeadAddressMixin(BaseModel):
    cep: Optional[str] = None
    logradouro: Optional[str] = None
    numero: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @field_validator("cep", mode="before")
    @classmethod
    def sanitize_cep(cls, value: object) -> object:
        if value is None:
            return None
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        return digits or None

    @field_validator("estado", mode="before")
    @classmethod
    def normalize_estado(cls, value: object) -> object:
        if value is None:
            return None
        cleaned = str(value).strip().upper()
        return cleaned or None


class LeadCreateIn(LeadAddressMixin):
    nome: str
    telefone: Optional[str] = None
    email: Optional[EmailStr] = None
    origem: Optional[str] = None
    owner_id: Optional[str] = None
    etapa: Stage = "novo"

    @model_validator(mode="after")
    def require_contact(self) -> "LeadCreateIn":
        if not self.telefone and not self.email:
            raise ValueError("telefone ou email é obrigatório")
        return self


class LeadUpdateIn(LeadAddressMixin):
    nome: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[EmailStr] = None
    origem: Optional[str] = None
    owner_id: Optional[str] = None
    etapa: Optional[Stage] = None

    @model_validator(mode="after")
    def require_any_field(self) -> "LeadUpdateIn":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo para atualizar")
        return self


class LeadOut(LeadAddressMixin):
    id: str
    org_id: str
    nome: str
    telefone: Optional[str] = None
    email: Optional[EmailStr] = None
    origem: Optional[str] = None
    owner_id: Optional[str] = None
    etapa: Optional[Stage] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    first_contact_at: Optional[str] = None
    address_updated_at: Optional[str] = None
