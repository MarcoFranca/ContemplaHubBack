"""WhatsApp Cloud API (oficial) - Fase 1: conexão da conta + template.

Fluxo de conexão (Embedded Signup):
1. Frontend inicia o FB JS SDK com `config_id` e recebe um `code` + `waba_id` + `phone_number_id`.
2. Frontend chama `POST /whatsapp/connect` com esses valores.
3. Aqui trocamos o `code` por um token de negócio, buscamos os dados do número e da
   WABA, inscrevemos o app nos webhooks da WABA e persistimos a integração da org.

O `access_token` fica só no backend, nunca é exposto ao frontend.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from fastapi import HTTPException, status
from supabase import Client

from app.core.config import settings

logger = logging.getLogger(__name__)

WHATSAPP_PROVIDER = "meta_cloud"
DEFAULT_TEMPLATE_KEY = "lead_welcome"
DEFAULT_TEMPLATE_BODY = (
    "Olá {{1}}! Recebemos seu contato e um especialista já vai falar com você. "
    "Enquanto isso, pode nos contar qual seu objetivo com o consórcio?"
)


def _trim(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _graph_base() -> str:
    return settings.META_GRAPH_API_BASE.rstrip("/")


def _graph_version() -> str:
    # extrai o "vXX.0" do final da base configurada
    tail = _graph_base().rsplit("/", 1)[-1]
    return tail if tail.startswith("v") else "v22.0"


def _require_meta_app() -> tuple[str, str]:
    app_id = settings.WHATSAPP_APP_ID.strip()
    app_secret = settings.WHATSAPP_APP_SECRET.strip()
    if not app_id or not app_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credenciais do app de WhatsApp não configuradas (WHATSAPP_APP_ID/WHATSAPP_APP_SECRET).",
        )
    return app_id, app_secret


def _graph_get(*, path: str, access_token: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    resp = requests.get(
        f"{_graph_base()}/{path.lstrip('/')}",
        params={**(params or {}), "access_token": access_token},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"WhatsApp Graph GET {path} falhou: {resp.status_code} {resp.text}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("WhatsApp Graph retornou payload inválido.")
    return payload


def _graph_post(*, path: str, access_token: str, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    resp = requests.post(
        f"{_graph_base()}/{path.lstrip('/')}",
        params={"access_token": access_token},
        data=data or {},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"WhatsApp Graph POST {path} falhou: {resp.status_code} {resp.text}")
    payload = resp.json()
    return payload if isinstance(payload, dict) else {}


# --------------------------------------------------------------------------- #
# Conexão (Embedded Signup)
# --------------------------------------------------------------------------- #
def signup_config(*, connected: bool) -> dict[str, Any]:
    app_id, _ = _require_meta_app()
    config_id = settings.WHATSAPP_ES_CONFIG_ID.strip()
    return {
        "ok": True,
        "app_id": app_id,
        "config_id": config_id,
        "graph_version": _graph_version(),
        "connected": connected,
    }


def exchange_signup_code(*, code: str) -> str:
    """Troca o code do Embedded Signup por um token de negócio (business token)."""
    app_id, app_secret = _require_meta_app()
    resp = requests.get(
        f"{_graph_base()}/oauth/access_token",
        params={
            "client_id": app_id,
            "client_secret": app_secret,
            "code": code,
        },
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"WhatsApp OAuth falhou: {resp.status_code} {resp.text}")
    payload = resp.json()
    token = _trim(payload.get("access_token")) if isinstance(payload, dict) else ""
    if not token:
        raise RuntimeError("WhatsApp OAuth não retornou access_token.")
    return token


def fetch_phone_number(*, access_token: str, phone_number_id: str) -> dict[str, Any]:
    return _graph_get(
        path=phone_number_id,
        access_token=access_token,
        params={"fields": "id,display_phone_number,verified_name,quality_rating,messaging_limit_tier"},
    )


def fetch_waba(*, access_token: str, waba_id: str) -> dict[str, Any]:
    return _graph_get(
        path=waba_id,
        access_token=access_token,
        params={"fields": "id,name,currency,timezone_id,owner_business_info"},
    )


def subscribe_app_to_waba(*, access_token: str, waba_id: str) -> None:
    """Inscreve o app nos webhooks da WABA (necessário para receber status/inbound)."""
    try:
        _graph_post(path=f"{waba_id}/subscribed_apps", access_token=access_token)
    except Exception as exc:  # noqa: BLE001 - não bloquear a conexão por causa disso
        logger.warning("whatsapp_subscribe_app_failed", extra={"waba_id": waba_id, "error": str(exc)})


def _sanitize_integration(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return row
    clean = dict(row)
    clean.pop("access_token", None)
    clean.pop("verify_token", None)
    clean.pop("settings", None)
    return clean


def get_integration_row(*, supa: Client, org_id: str) -> Optional[dict[str, Any]]:
    resp = (
        supa.table("whatsapp_integrations")
        .select("*")
        .eq("org_id", org_id)
        .order("ativo", desc=True)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def connect_integration(
    *,
    supa: Client,
    org_id: str,
    user_id: str,
    code: str,
    waba_id: str,
    phone_number_id: str,
) -> dict[str, Any]:
    """Conexão via Embedded Signup: troca o code por token e persiste."""
    access_token = exchange_signup_code(code=code)
    return _persist_integration(
        supa=supa,
        org_id=org_id,
        user_id=user_id,
        access_token=access_token,
        waba_id=waba_id,
        phone_number_id=phone_number_id,
    )


def connect_integration_manual(
    *,
    supa: Client,
    org_id: str,
    user_id: str,
    access_token: str,
    waba_id: str,
    phone_number_id: str,
) -> dict[str, Any]:
    """Conexão manual: usa um token já obtido (número de teste ou system user)."""
    token = _trim(access_token)
    if not token:
        raise HTTPException(status_code=400, detail="Token de acesso é obrigatório.")
    return _persist_integration(
        supa=supa,
        org_id=org_id,
        user_id=user_id,
        access_token=token,
        waba_id=waba_id,
        phone_number_id=phone_number_id,
    )


def _persist_integration(
    *,
    supa: Client,
    org_id: str,
    user_id: str,
    access_token: str,
    waba_id: str,
    phone_number_id: str,
) -> dict[str, Any]:
    # dados do número e da WABA (best-effort; conexão não falha se algum enriquecimento falhar)
    display_phone_number = None
    verified_name = None
    quality_rating = None
    messaging_limit = None
    business_id = None
    try:
        phone = fetch_phone_number(access_token=access_token, phone_number_id=phone_number_id)
        display_phone_number = phone.get("display_phone_number")
        verified_name = phone.get("verified_name")
        quality_rating = phone.get("quality_rating")
        messaging_limit = phone.get("messaging_limit_tier")
    except Exception as exc:  # noqa: BLE001
        logger.warning("whatsapp_fetch_phone_failed", extra={"error": str(exc)})

    try:
        waba = fetch_waba(access_token=access_token, waba_id=waba_id)
        owner = waba.get("owner_business_info") if isinstance(waba, dict) else None
        if isinstance(owner, dict):
            business_id = owner.get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("whatsapp_fetch_waba_failed", extra={"error": str(exc)})

    subscribe_app_to_waba(access_token=access_token, waba_id=waba_id)

    verify_token = settings.WHATSAPP_VERIFY_TOKEN.strip() or None

    payload = {
        "org_id": org_id,
        "updated_by": user_id,
        "provider": WHATSAPP_PROVIDER,
        "waba_id": waba_id,
        "business_id": business_id,
        "phone_number_id": phone_number_id,
        "display_phone_number": display_phone_number,
        "verified_name": verified_name,
        "access_token": access_token,
        "verify_token": verify_token,
        "quality_rating": quality_rating,
        "messaging_limit": messaging_limit,
        "ativo": True,
        "last_success_at": datetime.now(timezone.utc).isoformat(),
    }

    existing = (
        supa.table("whatsapp_integrations")
        .select("id")
        .eq("org_id", org_id)
        .eq("phone_number_id", phone_number_id)
        .limit(1)
        .execute()
    )
    existing_rows = getattr(existing, "data", None) or []

    if existing_rows:
        supa.table("whatsapp_integrations").update(payload).eq("id", existing_rows[0]["id"]).execute()
        row_id = existing_rows[0]["id"]
    else:
        payload["created_by"] = user_id
        inserted = supa.table("whatsapp_integrations").insert(payload).execute()
        inserted_rows = getattr(inserted, "data", None) or []
        row_id = inserted_rows[0]["id"] if inserted_rows else None

    ensure_default_template(supa=supa, org_id=org_id, user_id=user_id)

    row = get_integration_row(supa=supa, org_id=org_id)
    if row_id and (not row or row.get("id") != row_id):
        fetched = supa.table("whatsapp_integrations").select("*").eq("id", row_id).limit(1).execute()
        fetched_rows = getattr(fetched, "data", None) or []
        row = fetched_rows[0] if fetched_rows else row
    return _sanitize_integration(row or {})


def sanitize_integration_or_none(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return _sanitize_integration(row) if row else None


def deactivate_integration(*, supa: Client, org_id: str, integration_id: str) -> None:
    supa.table("whatsapp_integrations").update({"ativo": False}).eq("id", integration_id).eq(
        "org_id", org_id
    ).execute()


# --------------------------------------------------------------------------- #
# Template configurável por org
# --------------------------------------------------------------------------- #
def ensure_default_template(*, supa: Client, org_id: str, user_id: Optional[str] = None) -> dict[str, Any]:
    existing = (
        supa.table("whatsapp_templates")
        .select("*")
        .eq("org_id", org_id)
        .eq("key", DEFAULT_TEMPLATE_KEY)
        .limit(1)
        .execute()
    )
    rows = getattr(existing, "data", None) or []
    if rows:
        return rows[0]

    payload = {
        "org_id": org_id,
        "created_by": user_id,
        "updated_by": user_id,
        "key": DEFAULT_TEMPLATE_KEY,
        "language": "pt_BR",
        "category": "utility",
        "body_text": DEFAULT_TEMPLATE_BODY,
        "variables": ["nome"],
        "ativo": True,
    }
    inserted = supa.table("whatsapp_templates").insert(payload).execute()
    inserted_rows = getattr(inserted, "data", None) or []
    return inserted_rows[0] if inserted_rows else payload


def get_template(*, supa: Client, org_id: str, user_id: Optional[str] = None) -> dict[str, Any]:
    return ensure_default_template(supa=supa, org_id=org_id, user_id=user_id)


def update_template(
    *, supa: Client, org_id: str, user_id: str, changes: dict[str, Any]
) -> dict[str, Any]:
    current = ensure_default_template(supa=supa, org_id=org_id, user_id=user_id)
    update_payload = {k: v for k, v in changes.items() if v is not None}
    if not update_payload:
        return current
    update_payload["updated_by"] = user_id
    supa.table("whatsapp_templates").update(update_payload).eq("id", current["id"]).execute()
    fetched = supa.table("whatsapp_templates").select("*").eq("id", current["id"]).limit(1).execute()
    rows = getattr(fetched, "data", None) or []
    return rows[0] if rows else {**current, **update_payload}
