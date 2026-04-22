from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


PROVIDER_VALUES = ("meta", "meta_lead_ads")


class MetaIntegrationBaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    nome: str = Field(min_length=2, max_length=120)
    page_id: str = Field(min_length=1, max_length=120)
    page_name: Optional[str] = Field(default=None, max_length=160)
    form_id: Optional[str] = Field(default=None, max_length=120)
    form_name: Optional[str] = Field(default=None, max_length=160)
    source_label: str = Field(min_length=2, max_length=160)
    default_owner_id: Optional[str] = None
    ativo: bool = True
    verify_token: Optional[str] = Field(default=None, max_length=255)
    access_token: Optional[str] = Field(default=None, max_length=2048)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("page_id", "form_id", "default_owner_id", mode="before")
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("page_name", "form_name", "verify_token", "access_token", mode="before")
    @classmethod
    def optional_text_empty_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value


class MetaIntegrationCreateIn(MetaIntegrationBaseIn):
    verify_token: str = Field(min_length=6, max_length=255)
    access_token: str = Field(min_length=20, max_length=2048)


class MetaIntegrationUpdateIn(MetaIntegrationBaseIn):
    nome: Optional[str] = Field(default=None, min_length=2, max_length=120)
    page_id: Optional[str] = Field(default=None, min_length=1, max_length=120)
    source_label: Optional[str] = Field(default=None, min_length=2, max_length=160)
    ativo: Optional[bool] = None


class MetaIntegrationOut(BaseModel):
    id: str
    org_id: str
    nome: str
    provider: str
    page_id: str
    page_name: Optional[str] = None
    form_id: Optional[str] = None
    form_name: Optional[str] = None
    source_label: str
    channel: str
    default_owner_id: Optional[str] = None
    ativo: bool
    last_webhook_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    last_error_message: Optional[str] = None
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MetaWebhookEventOut(BaseModel):
    id: str
    org_id: Optional[str] = None
    integration_id: Optional[str] = None
    provider: str
    event_id: Optional[str] = None
    page_id: Optional[str] = None
    form_id: Optional[str] = None
    leadgen_id: Optional[str] = None
    event_type: str
    status: str
    error_message: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    processed_at: Optional[str] = None
    created_at: Optional[str] = None

