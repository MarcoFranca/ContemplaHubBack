from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class WhatsappSignupConfigOut(BaseModel):
    """Dados públicos para o frontend iniciar o Embedded Signup (FB JS SDK)."""

    ok: bool = True
    app_id: str
    config_id: str
    graph_version: str
    connected: bool = False


class WhatsappConnectIn(BaseModel):
    """Payload do frontend após o Embedded Signup retornar o code + IDs."""

    code: str = Field(min_length=1)
    waba_id: str = Field(min_length=1)
    phone_number_id: str = Field(min_length=1)


class WhatsappTestSendIn(BaseModel):
    """Envio de teste imediato (valida a conexão sem esperar o cron)."""

    to: str = Field(min_length=8)


class WhatsappDispatchOut(BaseModel):
    processed: int
    sent: int
    failed: int
    skipped: int


class WhatsappManualConnectIn(BaseModel):
    """Conexão manual (número de teste ou system user): token + IDs colados pelo admin."""

    access_token: str = Field(min_length=1)
    waba_id: str = Field(min_length=1)
    phone_number_id: str = Field(min_length=1)


class WhatsappIntegrationOut(BaseModel):
    id: UUID
    org_id: UUID
    provider: str
    waba_id: Optional[str] = None
    business_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    display_phone_number: Optional[str] = None
    verified_name: Optional[str] = None
    quality_rating: Optional[str] = None
    messaging_limit: Optional[str] = None
    ativo: bool
    last_webhook_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_error_at: Optional[datetime] = None
    last_error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class WhatsappDeleteOut(BaseModel):
    ok: bool = True
    id: UUID


class WhatsappTemplateOut(BaseModel):
    id: UUID
    org_id: UUID
    key: str
    template_name: Optional[str] = None
    language: str
    category: str
    body_text: Optional[str] = None
    variables: list[str] = []
    approval_status: Optional[str] = None
    ativo: bool
    created_at: datetime
    updated_at: datetime


class WhatsappTemplateUpdateIn(BaseModel):
    template_name: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    body_text: Optional[str] = None
    variables: Optional[list[str]] = None
    ativo: Optional[bool] = None
