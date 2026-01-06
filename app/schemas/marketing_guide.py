# app/schemas/marketing_guide.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Literal


class GuideSubmitIn(BaseModel):
    """
    Payload enviado pela landing.
    Importante: é um LEAD (visitante), não um usuário autenticado.
    """
    landing_slug: Optional[str] = Field(default=None, description="Slug da landing (se existir).")
    landing_hash: Optional[str] = Field(default=None, description="Hash público da landing (se existir).")

    nome: str = Field(min_length=2)
    telefone: str = Field(min_length=8)
    email: Optional[EmailStr] = None

    consentimento: bool = True
    consent_scope: str = Field(default="guia_estrategico_consorcio")

    # UTM (opcional)
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_term: Optional[str] = None
    utm_content: Optional[str] = None

    # Contexto (opcional)
    referrer_url: Optional[str] = None
    user_agent: Optional[str] = None


class GuideSubmitOut(BaseModel):
    lead_id: str


class GuideDownloadOut(BaseModel):
    # Se preferirem redirect no backend, este modelo não é necessário.
    signed_url: str
    expires_in_seconds: int = 300


class GuideDownloadMode(BaseModel):
    mode: Literal["redirect", "json"] = "redirect"
