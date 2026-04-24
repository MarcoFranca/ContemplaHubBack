from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse

import requests
from fastapi import HTTPException, status
from supabase import Client

from app.core.config import settings
from app.schemas.meta import PROVIDER_VALUES


META_PROVIDER = "meta_lead_ads"
META_PROVIDER_ALIASES = list(PROVIDER_VALUES)
META_CHANNEL = "meta_ads"
META_EVENT_TYPE = "meta_leadgen"
META_GRAPH_FIELDS = (
    "id,created_time,field_data,ad_id,ad_name,campaign_id,campaign_name,form_id,platform"
)
logger = logging.getLogger(__name__)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_data(resp: Any) -> Any:
    return getattr(resp, "data", None)


def _trim(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _mask_logged_token(value: Any) -> str:
    cleaned = _trim(value)
    if not cleaned:
        return "<missing>"
    if len(cleaned) <= 8:
        return f"{cleaned[:2]}***"
    return f"{cleaned[:4]}***{cleaned[-4:]}"


def _stringify_exception(exc: Exception) -> str:
    message = str(exc).strip()
    return message or repr(exc)


def _looks_like_missing_relation_error(message: str) -> bool:
    lowered = message.lower()
    return (
        "does not exist" in lowered
        or "relation" in lowered and "exist" in lowered
        or "schema cache" in lowered
        or "column" in lowered and "exist" in lowered
    )


def normalize_phone(value: Any) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits or None


def normalize_email(value: Any) -> Optional[str]:
    raw = _trim(value)
    return raw.lower() if raw else None


def _field_key(value: Any) -> str:
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum() or ch == "_")


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        cleaned = _trim(value)
        if cleaned:
            return cleaned
    return None


def _integration_provider_filter(builder: Any) -> Any:
    return builder.in_("provider", META_PROVIDER_ALIASES)


def _settings_dict(integration: dict[str, Any]) -> dict[str, Any]:
    settings_value = integration.get("settings")
    return settings_value if isinstance(settings_value, dict) else {}


def _validated_backend_public_url() -> str:
    raw_value = settings.BACKEND_PUBLIC_URL.strip()
    if not raw_value:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "BACKEND_PUBLIC_URL não configurado para o OAuth da Meta. "
                "Use a URL pública HTTPS do backend, sem localhost e sem barra final."
            ),
        )

    normalized = raw_value.rstrip("/")
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "BACKEND_PUBLIC_URL inválido para o OAuth da Meta. "
                "Use exatamente a URL pública HTTPS do backend."
            ),
        )
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "BACKEND_PUBLIC_URL inválido para o OAuth da Meta. "
                "Não use localhost; configure a URL pública HTTPS do backend."
            ),
        )
    if parsed.path not in {"", "/"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "BACKEND_PUBLIC_URL inválido para o OAuth da Meta. "
                "Informe apenas a origem pública do backend, sem path adicional."
            ),
        )

    frontend_value = settings.FRONTEND_SITE_URL.strip().rstrip("/")
    if frontend_value and normalized.lower() == frontend_value.lower():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "BACKEND_PUBLIC_URL inválido para o OAuth da Meta. "
                "Ele não pode apontar para o domínio do frontend."
            ),
        )

    return normalized


def _meta_oauth_redirect_uri() -> str:
    return f"{_validated_backend_public_url()}/meta/oauth/callback"


def _frontend_meta_integrations_url() -> str:
    return f"{settings.FRONTEND_SITE_URL.rstrip('/')}/app/meta-integracoes"


def _webhook_configured(integration: dict[str, Any]) -> bool:
    return bool(settings.META_VERIFY_TOKEN.strip() or _trim(integration.get("verify_token")))


def _access_token_configured(integration: dict[str, Any]) -> bool:
    return bool(_trim(integration.get("access_token_encrypted")))


def _subscription_settings(integration: dict[str, Any]) -> dict[str, Any]:
    raw = _settings_dict(integration).get("subscription")
    return raw if isinstance(raw, dict) else {}


def _connection_settings(integration: dict[str, Any]) -> dict[str, Any]:
    raw = _settings_dict(integration).get("connection")
    return raw if isinstance(raw, dict) else {}


def _oauth_draft_settings(integration: dict[str, Any]) -> dict[str, Any]:
    raw = _settings_dict(integration).get("oauth_draft")
    return raw if isinstance(raw, dict) else {}


def _is_active_oauth_draft(
    integration: dict[str, Any],
    *,
    user_id: Optional[str] = None,
) -> bool:
    oauth_draft = _oauth_draft_settings(integration)
    if not oauth_draft.get("active"):
        return False
    if user_id and oauth_draft.get("oauth_user_id") not in {None, user_id}:
        return False
    if user_id and integration.get("created_by") not in {None, user_id}:
        return False
    return True


def build_meta_integration_status(integration: dict[str, Any]) -> dict[str, Any]:
    subscription = _subscription_settings(integration)
    connection = _connection_settings(integration)
    return {
        "webhook_configured": _webhook_configured(integration),
        "access_token_configured": _access_token_configured(integration),
        "page_subscribed": subscription.get("subscribed"),
        "subscription_checked_at": subscription.get("checked_at"),
        "subscription_error": subscription.get("error"),
        "connection_ok": connection.get("ok"),
        "connection_checked_at": connection.get("checked_at"),
        "connection_error": connection.get("error"),
    }


def _oauth_state_secret() -> str:
    return (
        settings.META_APP_SECRET.strip()
        or settings.SUPABASE_SERVICE_ROLE_KEY.strip()
        or settings.META_VERIFY_TOKEN.strip()
    )


def create_meta_oauth_state(*, org_id: str, user_id: str) -> str:
    secret = _oauth_state_secret()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Segredo de OAuth Meta não configurado.",
        )
    payload = {
        "org_id": org_id,
        "user_id": user_id,
        "nonce": secrets.token_urlsafe(18),
        "issued_at": utcnow_iso(),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    signature = hashlib.sha256(f"{raw}.{secret}".encode("utf-8")).hexdigest()
    return json.dumps({"payload": payload, "sig": signature}, separators=(",", ":"))


def parse_meta_oauth_state(state_value: str) -> dict[str, Any]:
    secret = _oauth_state_secret()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Segredo de OAuth Meta não configurado.",
        )
    try:
        state = json.loads(state_value)
        payload = state["payload"]
        sig = state["sig"]
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="state inválido.")

    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    expected = hashlib.sha256(f"{raw}.{secret}".encode("utf-8")).hexdigest()
    if not secrets.compare_digest(expected, sig):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="state inválido.")
    if not payload.get("org_id") or not payload.get("user_id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="state inválido.")
    return payload


def build_meta_oauth_authorize_url(*, org_id: str, user_id: str) -> str:
    if not settings.META_APP_ID.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="META_APP_ID não configurado.",
        )
    backend_public_url = _validated_backend_public_url()
    redirect_uri = f"{backend_public_url}/meta/oauth/callback"
    state = create_meta_oauth_state(org_id=org_id, user_id=user_id)
    params = urlencode(
        {
            "client_id": settings.META_APP_ID.strip(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": settings.META_OAUTH_SCOPES,
            "state": state,
        }
    )
    auth_url = f"https://www.facebook.com/v22.0/dialog/oauth?{params}"
    logger.info(
        "meta_oauth_authorize_url_built",
        extra={
            "BACKEND_PUBLIC_URL": backend_public_url,
            "redirect_uri": redirect_uri,
            "auth_url": auth_url,
            "org_id": org_id,
            "user_id": user_id,
        },
    )
    return auth_url


def insert_audit_log(
    supa: Client,
    *,
    org_id: Optional[str],
    actor_id: Optional[str],
    entity: str,
    entity_id: Optional[str],
    action: str,
    diff: Optional[dict[str, Any]] = None,
) -> None:
    try:
        supa.table("audit_logs").insert(
            {
                "org_id": org_id,
                "actor_id": actor_id,
                "entity": entity,
                "entity_id": entity_id,
                "action": action,
                "diff": diff or {},
            }
        ).execute()
    except Exception:
        pass


def _update_integration_status(
    supa: Client,
    *,
    integration_id: str,
    updates: dict[str, Any],
) -> None:
    payload = {**updates, "updated_at": utcnow_iso()}
    supa.table("meta_lead_integrations").update(payload).eq("id", integration_id).execute()


def _merge_integration_settings(
    supa: Client,
    *,
    integration: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    next_settings = {**_settings_dict(integration), **updates}
    _update_integration_status(
        supa,
        integration_id=integration["id"],
        updates={"settings": next_settings},
    )
    integration["settings"] = next_settings
    return integration


def _insert_webhook_event(
    supa: Client,
    *,
    org_id: Optional[str],
    integration_id: Optional[str],
    payload: dict[str, Any],
    page_id: Optional[str],
    form_id: Optional[str],
    leadgen_id: Optional[str],
    event_id: Optional[str],
    status_value: str,
    error_message: Optional[str] = None,
) -> dict[str, Any]:
    event_payload = {
        "org_id": org_id,
        "integration_id": integration_id,
        "provider": META_PROVIDER,
        "event_id": event_id,
        "page_id": page_id,
        "form_id": form_id,
        "leadgen_id": leadgen_id,
        "event_type": META_EVENT_TYPE,
        "status": status_value,
        "error_message": error_message,
        "payload": payload,
    }
    resp = (
        supa.table("meta_webhook_events")
        .insert(event_payload)
        .select("*")
        .single()
        .execute()
    )
    data = _safe_data(resp)
    if not data:
        raise RuntimeError("Falha ao registrar meta_webhook_event.")
    return data


def _patch_webhook_event(
    supa: Client,
    *,
    event_row_id: str,
    status_value: str,
    error_message: Optional[str] = None,
) -> None:
    payload: dict[str, Any] = {
        "status": status_value,
        "processed_at": utcnow_iso(),
        "error_message": error_message,
    }
    supa.table("meta_webhook_events").update(payload).eq("id", event_row_id).execute()


def _build_event_id(payload: dict[str, Any], page_id: Optional[str], form_id: Optional[str], leadgen_id: Optional[str]) -> str:
    raw = json.dumps(
        {
            "page_id": page_id,
            "form_id": form_id,
            "leadgen_id": leadgen_id,
            "created_time": payload.get("created_time"),
            "payload": payload,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_owner_in_org(supa: Client, *, org_id: str, owner_id: Optional[str]) -> Optional[str]:
    if not owner_id:
        return None

    resp = (
        supa.table("profiles")
        .select("user_id")
        .eq("org_id", org_id)
        .eq("user_id", owner_id)
        .maybe_single()
        .execute()
    )
    row = _safe_data(resp)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="default_owner_id inválido para a organização informada.",
        )
    return owner_id


def resolve_meta_integration(
    supa: Client,
    *,
    page_id: str,
    form_id: str | None,
) -> Optional[dict[str, Any]]:
    if form_id:
        exact = (
            _integration_provider_filter(
                supa.table("meta_lead_integrations")
                .select("*")
                .eq("ativo", True)
                .eq("page_id", page_id)
                .eq("form_id", form_id)
            )
            .limit(1)
            .execute()
        )
        rows = _safe_data(exact) or []
        if rows:
            return rows[0]

    generic = (
        _integration_provider_filter(
            supa.table("meta_lead_integrations")
            .select("*")
            .eq("ativo", True)
            .eq("page_id", page_id)
            .is_("form_id", None)
        )
        .limit(1)
        .execute()
    )
    rows = _safe_data(generic) or []
    if rows:
        return rows[0]

    page_only = (
        _integration_provider_filter(
            supa.table("meta_lead_integrations")
            .select("*")
            .eq("ativo", True)
            .eq("page_id", page_id)
        )
        .limit(1)
        .execute()
    )
    rows = _safe_data(page_only) or []
    return rows[0] if rows else None


def resolve_meta_verify_token(
    supa: Client,
    *,
    verify_token: str,
) -> Optional[dict[str, Any]]:
    resp = (
        _integration_provider_filter(
            supa.table("meta_lead_integrations")
            .select("id, org_id, verify_token")
            .eq("ativo", True)
            .eq("verify_token", verify_token)
        )
        .limit(1)
        .execute()
    )
    rows = _safe_data(resp) or []
    return rows[0] if rows else None


def _fetch_meta_lead_details(
    *,
    leadgen_id: str,
    access_token: str,
) -> dict[str, Any]:
    response = requests.get(
        f"{settings.META_GRAPH_API_BASE.rstrip('/')}/{leadgen_id}",
        params={
            "access_token": access_token,
            "fields": META_GRAPH_FIELDS,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Meta Graph falhou: {response.status_code} {response.text}")

    data = response.json()
    if not isinstance(data, dict) or not data.get("id"):
        raise RuntimeError("Meta Graph não retornou um lead válido.")
    return data


def _meta_graph_request(
    *,
    method: str,
    path: str,
    access_token: str,
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    response = requests.request(
        method=method.upper(),
        url=f"{settings.META_GRAPH_API_BASE.rstrip('/')}/{path.lstrip('/')}",
        params={
            **(params or {}),
            "access_token": access_token,
        },
        data=data,
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Meta Graph falhou: {response.status_code} {response.text}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Meta Graph retornou payload inválido.")
    return payload


def _meta_graph_user_request(
    *,
    method: str,
    path: str,
    user_access_token: str,
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return _meta_graph_request(
        method=method,
        path=path,
        access_token=user_access_token,
        params=params,
        data=data,
    )


def exchange_meta_oauth_code(*, code: str) -> str:
    if not settings.META_APP_ID.strip() or not settings.META_APP_SECRET.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credenciais OAuth da Meta não configuradas.",
        )
    backend_public_url = _validated_backend_public_url()
    redirect_uri = f"{backend_public_url}/meta/oauth/callback"
    logger.info(
        "meta_oauth_exchange_started",
        extra={
            "BACKEND_PUBLIC_URL": backend_public_url,
            "redirect_uri": redirect_uri,
            "code_present": bool(code),
        },
    )
    response = requests.get(
        f"{settings.META_GRAPH_API_BASE.rstrip('/')}/oauth/access_token",
        params={
            "client_id": settings.META_APP_ID.strip(),
            "client_secret": settings.META_APP_SECRET.strip(),
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Meta OAuth falhou: {response.status_code} {response.text}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Meta OAuth retornou payload inválido.")
    access_token = _trim(payload.get("access_token"))
    if not access_token:
        raise RuntimeError("Meta OAuth não retornou access_token.")
    return access_token


def list_meta_oauth_pages(*, user_access_token: str) -> list[dict[str, Any]]:
    payload = _meta_graph_user_request(
        method="GET",
        path="me/accounts",
        user_access_token=user_access_token,
        params={"fields": "id,name,category,access_token"},
    )
    result: list[dict[str, Any]] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        result.append(
            {
                "id": str(item["id"]),
                "name": _trim(item.get("name")),
                "category": _trim(item.get("category")),
                "access_token": _trim(item.get("access_token")),
            }
        )
    return result


def _list_org_meta_integrations(
    supa: Client,
    *,
    org_id: str,
) -> list[dict[str, Any]]:
    resp = (
        _integration_provider_filter(
            supa.table("meta_lead_integrations")
            .select("*")
            .eq("org_id", org_id)
        )
        .order("updated_at", desc=True)
        .limit(200)
        .execute()
    )
    return _safe_data(resp) or []


def _oauth_draft_rows_for_user(
    supa: Client,
    *,
    org_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    return [
        row
        for row in _list_org_meta_integrations(supa, org_id=org_id)
        if _is_active_oauth_draft(row, user_id=user_id)
    ]


def get_latest_meta_oauth_draft(
    supa: Client,
    *,
    org_id: str,
    user_id: str,
) -> Optional[dict[str, Any]]:
    rows = _oauth_draft_rows_for_user(supa, org_id=org_id, user_id=user_id)
    return rows[0] if rows else None


def _get_meta_oauth_draft_for_page(
    supa: Client,
    *,
    org_id: str,
    user_id: str,
    page_id: str,
) -> Optional[dict[str, Any]]:
    rows = _oauth_draft_rows_for_user(supa, org_id=org_id, user_id=user_id)
    for row in rows:
        if str(row.get("page_id")) == str(page_id):
            return row
    return None


def _build_oauth_page_draft_payload(
    *,
    org_id: str,
    user_id: str,
    page: dict[str, Any],
    existing_row: Optional[dict[str, Any]],
    user_access_token: str,
) -> dict[str, Any]:
    page_id = str(page["id"])
    page_name = _trim(page.get("name"))
    existing_settings = _settings_dict(existing_row or {})
    return {
        "org_id": org_id,
        "created_by": (existing_row or {}).get("created_by") or user_id,
        "updated_by": user_id,
        "nome": (existing_row or {}).get("nome") or f"Meta {page_name or page_id}",
        "provider": META_PROVIDER,
        "page_id": page_id,
        "page_name": page_name,
        "form_id": (existing_row or {}).get("form_id"),
        "form_name": (existing_row or {}).get("form_name"),
        "source_label": (existing_row or {}).get("source_label") or "Meta Ads",
        "channel": META_CHANNEL,
        "default_owner_id": (existing_row or {}).get("default_owner_id"),
        "verify_token": (existing_row or {}).get("verify_token"),
        "access_token_encrypted": _trim(page.get("access_token")) or user_access_token,
        "ativo": bool((existing_row or {}).get("ativo", False)),
        "settings": {
            **existing_settings,
            "oauth_draft": {
                **_oauth_draft_settings(existing_row or {}),
                "active": True,
                "connected_at": utcnow_iso(),
                "oauth_user_id": user_id,
                "page_category": _trim(page.get("category")),
            },
        },
        "updated_at": utcnow_iso(),
    }


def save_meta_oauth_draft(
    supa: Client,
    *,
    org_id: str,
    user_id: str,
    user_access_token: str,
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_rows = _oauth_draft_rows_for_user(supa, org_id=org_id, user_id=user_id)
    rows_by_page_id = {
        str(row.get("page_id")): row for row in existing_rows if row.get("page_id")
    }
    persisted: list[dict[str, Any]] = []
    seen_page_ids: set[str] = set()

    for page in pages:
        page_id = str(page["id"])
        seen_page_ids.add(page_id)
        existing_row = rows_by_page_id.get(page_id)
        payload = _build_oauth_page_draft_payload(
            org_id=org_id,
            user_id=user_id,
            page=page,
            existing_row=existing_row,
            user_access_token=user_access_token,
        )
        logger.info(
            "meta_oauth_draft_persist_attempt",
            extra={
                "table": "meta_lead_integrations",
                "org_id": org_id,
                "user_id": user_id,
                "page_id": payload["page_id"],
                "page_name": payload.get("page_name"),
                "existing_id": (existing_row or {}).get("id"),
                "is_update": bool(existing_row),
                "fields": {
                    "nome": payload.get("nome"),
                    "source_label": payload.get("source_label"),
                    "channel": payload.get("channel"),
                    "ativo": payload.get("ativo"),
                    "provider": payload.get("provider"),
                    "default_owner_id": payload.get("default_owner_id"),
                    "access_token_masked": _mask_logged_token(payload.get("access_token_encrypted")),
                },
            },
        )

        try:
            if existing_row:
                resp = (
                    supa.table("meta_lead_integrations")
                    .update(payload)
                    .eq("id", existing_row["id"])
                    .eq("org_id", org_id)
                    .select("*")
                    .single()
                    .execute()
                )
            else:
                resp = (
                    supa.table("meta_lead_integrations")
                    .insert(payload)
                    .select("*")
                    .single()
                    .execute()
                )
        except Exception as exc:
            error_message = _stringify_exception(exc)
            logger.exception(
                "meta_oauth_draft_persist_failed",
                extra={
                    "table": "meta_lead_integrations",
                    "org_id": org_id,
                    "user_id": user_id,
                    "page_id": payload["page_id"],
                    "page_name": payload.get("page_name"),
                    "existing_id": (existing_row or {}).get("id"),
                    "is_update": bool(existing_row),
                    "error_type": exc.__class__.__name__,
                    "error_message": error_message,
                    "fields": {
                        "nome": payload.get("nome"),
                        "source_label": payload.get("source_label"),
                        "channel": payload.get("channel"),
                        "ativo": payload.get("ativo"),
                        "provider": payload.get("provider"),
                        "default_owner_id": payload.get("default_owner_id"),
                    },
                },
            )
            if _looks_like_missing_relation_error(error_message):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Estrutura de banco incompatível para a integração Meta. "
                        "Verifique se a tabela/colunas esperadas existem ou se falta migration."
                    ),
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Falha ao salvar integração temporária da página {page_id}: {error_message}",
            ) from exc
        row = _safe_data(resp)
        if not row:
            raise RuntimeError(
                f"Falha ao salvar integração temporária da página Meta {page_id}."
            )
        logger.info(
            "meta_oauth_draft_persist_success",
            extra={
                "table": "meta_lead_integrations",
                "org_id": org_id,
                "user_id": user_id,
                "page_id": row.get("page_id"),
                "page_name": row.get("page_name"),
                "integration_id": row.get("id"),
                "is_update": bool(existing_row),
            },
        )
        persisted.append(row)

    for row in existing_rows:
        page_id = str(row.get("page_id"))
        if page_id in seen_page_ids:
            continue
        next_settings = {
            **_settings_dict(row),
            "oauth_draft": {
                **_oauth_draft_settings(row),
                "active": False,
                "discarded_at": utcnow_iso(),
                "oauth_user_id": user_id,
            },
        }
        _update_integration_status(
            supa,
            integration_id=row["id"],
            updates={
                "settings": next_settings,
                "ativo": False,
            },
        )

    return persisted


def get_meta_oauth_pages_for_user(
    supa: Client,
    *,
    org_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    drafts = _oauth_draft_rows_for_user(supa, org_id=org_id, user_id=user_id)
    return [
        {
            "id": str(row["page_id"]),
            "name": _trim(row.get("page_name")),
            "category": _oauth_draft_settings(row).get("page_category"),
        }
        for row in drafts
        if row.get("page_id")
    ]


def get_meta_page_forms_for_user(
    supa: Client,
    *,
    org_id: str,
    user_id: str,
    page_id: str,
) -> list[dict[str, Any]]:
    draft = _get_meta_oauth_draft_for_page(
        supa,
        org_id=org_id,
        user_id=user_id,
        page_id=page_id,
    )
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma página Meta conectada foi encontrada para este usuário.",
        )
    access_token = _ensure_meta_integration_token(draft)
    payload = _meta_graph_user_request(
        method="GET",
        path=f"{page_id}/leadgen_forms",
        user_access_token=access_token,
        params={"fields": "id,name,status"},
    )
    result: list[dict[str, Any]] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        result.append(
            {
                "id": str(item["id"]),
                "name": _trim(item.get("name")),
                "status": _trim(item.get("status")),
            }
        )
    return result


def finalize_meta_oauth_integration(
    supa: Client,
    *,
    org_id: str,
    user_id: str,
    nome: str,
    source_label: str,
    page_id: str,
    form_id: Optional[str],
    default_owner_id: Optional[str],
    ativo: bool,
) -> dict[str, Any]:
    draft = _get_meta_oauth_draft_for_page(
        supa,
        org_id=org_id,
        user_id=user_id,
        page_id=page_id,
    )
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma página Meta conectada foi encontrada para este usuário.",
        )
    forms = get_meta_page_forms_for_user(
        supa,
        org_id=org_id,
        user_id=user_id,
        page_id=page_id,
    )
    selected_form = (
        next((item for item in forms if item["id"] == form_id), None) if form_id else None
    )
    if form_id and not selected_form:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formulário não encontrado para a página selecionada.",
    )

    payload = {
        "nome": nome,
        "page_id": page_id,
        "page_name": draft.get("page_name"),
        "form_id": form_id,
        "form_name": selected_form.get("name") if selected_form else None,
        "source_label": source_label,
        "default_owner_id": default_owner_id,
        "access_token_encrypted": _ensure_meta_integration_token(draft),
        "ativo": ativo,
        "updated_by": user_id,
        "updated_at": utcnow_iso(),
        "settings": {
            **_settings_dict(draft),
            "oauth_draft": {
                "active": False,
                "finalized_at": utcnow_iso(),
            },
        },
    }

    resp = (
        supa.table("meta_lead_integrations")
        .update(payload)
        .eq("id", draft["id"])
        .eq("org_id", org_id)
        .select("*")
        .single()
        .execute()
    )
    row = _safe_data(resp)
    if not row:
        raise RuntimeError("Falha ao finalizar integração Meta via OAuth.")
    return row


def _ensure_meta_integration_token(integration: dict[str, Any]) -> str:
    access_token = _trim(integration.get("access_token_encrypted"))
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integração Meta sem access_token configurado.",
        )
    return access_token


def _record_connection_result(
    supa: Client,
    *,
    integration: dict[str, Any],
    ok: bool,
    raw: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    checked_at = utcnow_iso()
    return _merge_integration_settings(
        supa,
        integration=integration,
        updates={
            "connection": {
                "ok": ok,
                "checked_at": checked_at,
                "error": error,
                "page_name": raw.get("name") if isinstance(raw, dict) else None,
            }
        },
    )


def _record_subscription_result(
    supa: Client,
    *,
    integration: dict[str, Any],
    subscribed: Optional[bool],
    raw: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    checked_at = utcnow_iso()
    return _merge_integration_settings(
        supa,
        integration=integration,
        updates={
            "subscription": {
                "subscribed": subscribed,
                "checked_at": checked_at,
                "error": error,
                "app_id": settings.META_APP_ID or None,
                "page_id": integration.get("page_id"),
                "raw_count": len((raw or {}).get("data") or [])
                if isinstance(raw, dict)
                else None,
            }
        },
    )


def test_meta_integration_connection(
    supa: Client,
    *,
    integration: dict[str, Any],
) -> dict[str, Any]:
    access_token = _ensure_meta_integration_token(integration)
    try:
        payload = _meta_graph_request(
            method="GET",
            path=integration["page_id"],
            access_token=access_token,
            params={"fields": "id,name"},
        )
        _record_connection_result(
            supa,
            integration=integration,
            ok=True,
            raw=payload,
            error=None,
        )
        return {
            "ok": True,
            "integration_id": integration["id"],
            "page_id": integration["page_id"],
            "page_name": payload.get("name") or integration.get("page_name"),
            "checked_at": utcnow_iso(),
            "raw": payload,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _record_connection_result(
            supa,
            integration=integration,
            ok=False,
            raw=None,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )


def get_meta_subscription_status(
    supa: Client,
    *,
    integration: dict[str, Any],
) -> dict[str, Any]:
    access_token = _ensure_meta_integration_token(integration)
    try:
        payload = _meta_graph_request(
            method="GET",
            path=f"{integration['page_id']}/subscribed_apps",
            access_token=access_token,
            params={"fields": "id,name"},
        )
        entries = payload.get("data") or []
        app_id = settings.META_APP_ID.strip()
        subscribed = any(
            str(item.get("id")) == app_id for item in entries if isinstance(item, dict)
        ) if app_id else bool(entries)
        _record_subscription_result(
            supa,
            integration=integration,
            subscribed=subscribed,
            raw=payload,
            error=None,
        )
        return {
            "ok": True,
            "integration_id": integration["id"],
            "page_id": integration["page_id"],
            "page_name": integration.get("page_name"),
            "subscribed": subscribed,
            "checked_at": utcnow_iso(),
            "app_id": app_id or None,
            "raw": payload,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _record_subscription_result(
            supa,
            integration=integration,
            subscribed=False,
            raw=None,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )


def subscribe_meta_page(
    supa: Client,
    *,
    integration: dict[str, Any],
) -> dict[str, Any]:
    access_token = _ensure_meta_integration_token(integration)
    try:
        payload = _meta_graph_request(
            method="POST",
            path=f"{integration['page_id']}/subscribed_apps",
            access_token=access_token,
            data={"subscribed_fields": "leadgen"},
        )
        _record_subscription_result(
            supa,
            integration=integration,
            subscribed=True,
            raw=payload,
            error=None,
        )
        return {
            "ok": True,
            "integration_id": integration["id"],
            "page_id": integration["page_id"],
            "subscribed": True,
            "checked_at": utcnow_iso(),
            "raw": payload,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _record_subscription_result(
            supa,
            integration=integration,
            subscribed=False,
            raw=None,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )


def list_meta_page_forms(
    *,
    integration: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        access_token = _ensure_meta_integration_token(integration)
        payload = _meta_graph_request(
            method="GET",
            path=f"{integration['page_id']}/leadgen_forms",
            access_token=access_token,
            params={"fields": "id,name,status"},
        )
        forms = payload.get("data") or []
        result: list[dict[str, Any]] = []
        for item in forms:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            result.append(
                {
                    "id": str(item["id"]),
                    "name": _trim(item.get("name")),
                    "status": _trim(item.get("status")),
                }
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )


def _parse_meta_field_data(field_data: list[dict[str, Any]] | None) -> dict[str, Optional[str]]:
    values: dict[str, Optional[str]] = {}

    for item in field_data or []:
        key = _field_key(item.get("name"))
        raw_values = item.get("values") or []
        first_value = None
        if isinstance(raw_values, list) and raw_values:
            first_value = raw_values[0]
        values[key] = _trim(first_value)

    full_name = _first_non_empty(
        values.get("fullname"),
        values.get("full_name"),
        values.get("nomecompleto"),
        values.get("nome_completo"),
        values.get("name"),
        values.get("nome"),
        "Lead Meta",
    )

    if full_name == "Lead Meta":
        first_name = _first_non_empty(values.get("firstname"), values.get("first_name"))
        last_name = _first_non_empty(values.get("lastname"), values.get("last_name"))
        full_name = _first_non_empty(" ".join(part for part in [first_name, last_name] if part), "Lead Meta")

    email = normalize_email(
        _first_non_empty(values.get("email"), values.get("emailaddress"), values.get("email_address"))
    )
    phone = normalize_phone(
        _first_non_empty(
            values.get("phone"),
            values.get("phonenumber"),
            values.get("phone_number"),
            values.get("telefone"),
            values.get("celular"),
            values.get("mobilephone"),
            values.get("mobile_phone"),
            values.get("whatsapp"),
        )
    )

    return {
        "nome": full_name,
        "email": email,
        "telefone": phone,
    }


def _fetch_existing_lead_by_contact(
    supa: Client,
    *,
    org_id: str,
    telefone: Optional[str],
    email: Optional[str],
) -> Optional[dict[str, Any]]:
    if telefone:
        phone_resp = (
            supa.table("leads")
            .select(
                "id, org_id, nome, telefone, email, origem, owner_id, etapa, "
                "source_label, form_label, channel, utm_source, utm_medium, utm_campaign, utm_term, utm_content"
            )
            .eq("org_id", org_id)
            .eq("telefone", telefone)
            .limit(1)
            .execute()
        )
        rows = _safe_data(phone_resp) or []
        if rows:
            return rows[0]

    if email:
        email_resp = (
            supa.table("leads")
            .select(
                "id, org_id, nome, telefone, email, origem, owner_id, etapa, "
                "source_label, form_label, channel, utm_source, utm_medium, utm_campaign, utm_term, utm_content"
            )
            .eq("org_id", org_id)
            .ilike("email", email)
            .limit(1)
            .execute()
        )
        rows = _safe_data(email_resp) or []
        if rows:
            return rows[0]

    return None


def upsert_lead_from_meta(
    supa: Client,
    *,
    integration: dict[str, Any],
    lead_payload: dict[str, Any],
    actor_id: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    org_id = integration["org_id"]
    telefone = normalize_phone(lead_payload.get("telefone"))
    email = normalize_email(lead_payload.get("email"))

    if not telefone and not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead da Meta sem telefone ou email para deduplicação.",
        )

    existing = _fetch_existing_lead_by_contact(
        supa,
        org_id=org_id,
        telefone=telefone,
        email=email,
    )

    owner_id = integration.get("default_owner_id")
    base_payload: dict[str, Any] = {
        "org_id": org_id,
        "nome": lead_payload["nome"],
        "telefone": telefone,
        "email": email,
        "origem": "meta_ads",
        "etapa": "novo",
        "source_label": integration.get("source_label"),
        "form_label": integration.get("form_name") or lead_payload.get("form_label"),
        "channel": integration.get("channel") or META_CHANNEL,
        "utm_source": lead_payload.get("utm_source") or "meta_ads",
        "utm_medium": lead_payload.get("utm_medium"),
        "utm_campaign": lead_payload.get("utm_campaign"),
        "utm_term": lead_payload.get("utm_term"),
        "utm_content": lead_payload.get("utm_content"),
        "referrer_url": lead_payload.get("referrer_url"),
        "user_agent": lead_payload.get("user_agent"),
    }

    if existing:
        update_payload = {
            key: value
            for key, value in base_payload.items()
            if value is not None
        }
        update_payload.pop("org_id", None)
        update_payload.pop("etapa", None)
        if existing.get("owner_id"):
            update_payload.pop("owner_id", None)
        elif owner_id:
            update_payload["owner_id"] = owner_id

        resp = (
            supa.table("leads")
            .update(update_payload)
            .eq("org_id", org_id)
            .eq("id", existing["id"])
            .select("*")
            .single()
            .execute()
        )
        row = _safe_data(resp)
        if not row:
            raise RuntimeError("Falha ao atualizar lead existente da Meta.")

        insert_audit_log(
            supa,
            org_id=org_id,
            actor_id=actor_id,
            entity="lead",
            entity_id=row["id"],
            action="meta_upsert_update",
            diff={
                "provider": META_PROVIDER,
                "integration_id": integration["id"],
                "matched_by": "telefone" if telefone and existing.get("telefone") == telefone else "email",
                "updated_fields": sorted(update_payload.keys()),
            },
        )
        return row, "updated"

    create_payload = {
        **base_payload,
        "owner_id": owner_id,
        "created_by": actor_id,
    }
    resp = (
        supa.table("leads")
        .insert(create_payload)
        .select("*")
        .single()
        .execute()
    )
    row = _safe_data(resp)
    if not row:
        raise RuntimeError("Falha ao criar lead da Meta.")

    insert_audit_log(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        entity="lead",
        entity_id=row["id"],
        action="meta_upsert_create",
        diff={
            "provider": META_PROVIDER,
            "integration_id": integration["id"],
            "source_label": integration.get("source_label"),
        },
    )
    return row, "created"


def publish_meta_lead_event_outbox(
    supa: Client,
    *,
    org_id: str,
    lead_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    supa.table("event_outbox").insert(
        {
            "org_id": org_id,
            "event_type": event_type,
            "aggregate_type": "lead",
            "aggregate_id": lead_id,
            "payload": payload,
            "status": "pending",
        }
    ).execute()


def ingest_meta_lead_event(
    supa: Client,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    page_id = _trim(payload.get("page_id"))
    form_id = _trim(payload.get("form_id"))
    leadgen_id = _trim(payload.get("leadgen_id"))

    if not page_id or not leadgen_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Evento Meta sem page_id ou leadgen_id.",
        )

    event_id = _build_event_id(payload, page_id, form_id, leadgen_id)
    integration = resolve_meta_integration(
        supa,
        page_id=page_id,
        form_id=form_id,
    )

    if not integration:
        _insert_webhook_event(
            supa,
            org_id=None,
            integration_id=None,
            payload=payload,
            page_id=page_id,
            form_id=form_id,
            leadgen_id=leadgen_id,
            event_id=event_id,
            status_value="error",
            error_message="Integração Meta não encontrada para page_id/form_id.",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integração Meta não encontrada para o payload informado.",
        )

    duplicate_resp = (
        supa.table("meta_webhook_events")
        .select("id, status, integration_id")
        .eq("integration_id", integration["id"])
        .eq("event_id", event_id)
        .limit(1)
        .execute()
    )
    duplicate_rows = _safe_data(duplicate_resp) or []
    if duplicate_rows:
        return {
            "ok": True,
            "event_id": duplicate_rows[0]["id"],
            "lead_id": None,
            "action": "duplicate_ignored",
        }

    event = _insert_webhook_event(
        supa,
        org_id=integration["org_id"],
        integration_id=integration["id"],
        payload=payload,
        page_id=page_id,
        form_id=form_id,
        leadgen_id=leadgen_id,
        event_id=event_id,
        status_value="received",
    )

    _update_integration_status(
        supa,
        integration_id=integration["id"],
        updates={
            "last_webhook_at": utcnow_iso(),
        },
    )

    try:
        access_token = _trim(integration.get("access_token_encrypted"))
        if not access_token:
            raise RuntimeError("Integração Meta sem access_token configurado.")

        lead_data = _fetch_meta_lead_details(
            leadgen_id=leadgen_id,
            access_token=access_token,
        )
        parsed = _parse_meta_field_data(lead_data.get("field_data"))
        if not parsed.get("nome"):
            raise RuntimeError("Lead da Meta sem nome válido.")

        meta_lead_payload = {
            **parsed,
            "utm_source": "meta_ads",
            "utm_medium": "lead_ads",
            "utm_campaign": _trim(lead_data.get("campaign_name")) or _trim(payload.get("campaign_name")),
            "utm_term": _trim(lead_data.get("form_id")) or form_id,
            "utm_content": _trim(lead_data.get("ad_name")) or _trim(payload.get("ad_name")),
            "form_label": integration.get("form_name") or _trim(payload.get("form_name")),
            "user_agent": None,
            "referrer_url": None,
        }

        lead_row, action = upsert_lead_from_meta(
            supa,
            integration=integration,
            lead_payload=meta_lead_payload,
            actor_id=integration.get("created_by") or integration.get("updated_by"),
        )

        publish_meta_lead_event_outbox(
            supa,
            org_id=integration["org_id"],
            lead_id=lead_row["id"],
            event_type=f"meta_lead_{action}",
            payload={
                "provider": META_PROVIDER,
                "integration_id": integration["id"],
                "lead_id": lead_row["id"],
                "leadgen_id": leadgen_id,
                "page_id": page_id,
                "form_id": form_id,
                "action": action,
            },
        )

        _patch_webhook_event(
            supa,
            event_row_id=event["id"],
            status_value="processed",
        )
        _update_integration_status(
            supa,
            integration_id=integration["id"],
            updates={
                "last_success_at": utcnow_iso(),
                "last_error_at": None,
                "last_error_message": None,
            },
        )
        insert_audit_log(
            supa,
            org_id=integration["org_id"],
            actor_id=integration.get("created_by") or integration.get("updated_by"),
            entity="meta_webhook_event",
            entity_id=event["id"],
            action="processed",
            diff={
                "integration_id": integration["id"],
                "lead_id": lead_row["id"],
                "action": action,
            },
        )
        return {
            "ok": True,
            "event_id": event["id"],
            "lead_id": lead_row["id"],
            "action": action,
        }
    except HTTPException as exc:
        _patch_webhook_event(
            supa,
            event_row_id=event["id"],
            status_value="error",
            error_message=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        )
        _update_integration_status(
            supa,
            integration_id=integration["id"],
            updates={
                "last_error_at": utcnow_iso(),
                "last_error_message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            },
        )
        raise
    except Exception as exc:
        message = str(exc)
        _patch_webhook_event(
            supa,
            event_row_id=event["id"],
            status_value="error",
            error_message=message,
        )
        _update_integration_status(
            supa,
            integration_id=integration["id"],
            updates={
                "last_error_at": utcnow_iso(),
                "last_error_message": message,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=message,
        )
