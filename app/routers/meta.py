from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Optional
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from supabase import Client

from app.core.config import settings
from app.deps import get_supabase_admin
from app.schemas.meta import (
    MetaIntegrationCreateIn,
    MetaConnectionTestOut,
    MetaIntegrationOut,
    MetaOAuthFinalizeIn,
    MetaOAuthStartOut,
    MetaPageFormOut,
    MetaPageOut,
    MetaSubscribePageOut,
    MetaSubscriptionStatusOut,
    MetaIntegrationUpdateIn,
    MetaWebhookEventOut,
)
from app.security.auth import AuthContext
from app.security.permissions import require_manager
from app.services.meta_leads_service import (
    META_CHANNEL,
    META_PROVIDER,
    _ensure_owner_in_org,
    _is_active_oauth_draft,
    _integration_provider_filter,
    _safe_data,
    build_meta_oauth_authorize_url,
    build_meta_integration_status,
    exchange_meta_oauth_code,
    finalize_meta_oauth_integration,
    get_meta_oauth_pages_for_user,
    get_meta_page_forms_for_user,
    get_meta_oauth_user_diagnostics,
    get_meta_subscription_status,
    ingest_meta_lead_event,
    insert_audit_log,
    list_meta_page_forms,
    list_meta_oauth_pages,
    parse_meta_oauth_state,
    resolve_meta_verify_token,
    save_meta_oauth_draft,
    subscribe_meta_page,
    test_meta_integration_connection,
    _validated_frontend_site_url,
    _frontend_meta_integrations_url,
    utcnow_iso,
)


router = APIRouter(tags=["meta"])
logger = logging.getLogger(__name__)


def _mask_token(value: Optional[str]) -> str:
    if not value:
        return "<missing>"
    cleaned = str(value).strip()
    if len(cleaned) <= 6:
        return f"{cleaned[:2]}***"
    return f"{cleaned[:3]}***{cleaned[-2:]}"


def _build_frontend_redirect_response(
    *,
    params: dict[str, str],
    log_event: str,
    level: str = "info",
    message_detail: Optional[str] = None,
):
    raw_frontend_site_url = settings.FRONTEND_SITE_URL
    try:
        frontend_site_url = _validated_frontend_site_url()
        redirect_base = _frontend_meta_integrations_url()
        redirect_url = f"{redirect_base}?{urlencode(params)}"
        logger_method = getattr(logger, level, logger.info)
        logger_method(
            f"{log_event}: {message_detail}" if message_detail else log_event,
            extra={
                "FRONTEND_SITE_URL": frontend_site_url,
                "redirect_url": redirect_url,
                "status_code": 302,
            },
        )
        return RedirectResponse(url=redirect_url, status_code=302)
    except Exception as exc:
        fallback_message = (
            "OAuth Meta concluído, mas o redirecionamento para o frontend falhou. "
            f"FRONTEND_SITE_URL atual: {raw_frontend_site_url or '<missing>'}. "
            f"Erro: {str(exc) or repr(exc)}"
        )
        logger.error(
            "meta_oauth_callback_redirect_failure",
            extra={
                "FRONTEND_SITE_URL": raw_frontend_site_url,
                "redirect_url": None,
                "error_type": exc.__class__.__name__,
                "error_message": str(exc) or repr(exc),
                "status_code": 500,
            },
        )
        return PlainTextResponse(content=fallback_message, status_code=500)


def _sanitize_integration(row: dict[str, Any]) -> dict[str, Any]:
    operational = build_meta_integration_status(row)
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "nome": row["nome"],
        "provider": row.get("provider") or META_PROVIDER,
        "page_id": row["page_id"],
        "page_name": row.get("page_name"),
        "form_id": row.get("form_id"),
        "form_name": row.get("form_name"),
        "source_label": row["source_label"],
        "channel": row.get("channel") or META_CHANNEL,
        "default_owner_id": row.get("default_owner_id"),
        "ativo": bool(row.get("ativo", False)),
        "last_webhook_at": row.get("last_webhook_at"),
        "last_success_at": row.get("last_success_at"),
        "last_error_at": row.get("last_error_at"),
        "last_error_message": row.get("last_error_message"),
        **operational,
        "settings": row.get("settings") or {},
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _get_integration_or_404(
    supa: Client,
    *,
    org_id: str,
    integration_id: str,
) -> dict[str, Any]:
    resp = (
        _integration_provider_filter(
            supa.table("meta_lead_integrations")
            .select("*")
            .eq("org_id", org_id)
            .eq("id", integration_id)
        )
        .maybe_single()
        .execute()
    )
    row = _safe_data(resp)
    if not row:
        raise HTTPException(status_code=404, detail="Integração Meta não encontrada.")
    return row


def _ensure_callback_user_in_org(
    supa: Client,
    *,
    org_id: str,
    user_id: str,
) -> None:
    resp = (
        supa.table("profiles")
        .select("user_id")
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not _safe_data(resp):
        raise HTTPException(
            status_code=403,
            detail="Usuário do callback OAuth não pertence à organização informada.",
        )


def _validate_meta_signature(
    *,
    body: bytes,
    signature_header: Optional[str],
) -> None:
    app_secret = settings.META_APP_SECRET.strip()
    if not app_secret:
        raise HTTPException(
            status_code=503,
            detail="META_APP_SECRET não configurado para validar assinatura do webhook.",
        )
    if not signature_header:
        raise HTTPException(status_code=403, detail="Assinatura do webhook ausente.")
    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=403, detail="Assinatura do webhook inválida.")

    expected = hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header.split("=", 1)[1].strip()
    if not hmac.compare_digest(expected, received):
        raise HTTPException(status_code=403, detail="Assinatura do webhook inválida.")


@router.get("/api/public/webhooks/meta/leadgen")
@router.get("/api/public/webhooks/meta/leadgen/", include_in_schema=False)
async def verify_meta_webhook(
    request: Request,
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
    supa: Client = Depends(get_supabase_admin),
):
    logger.info(
        "meta_webhook_verify_received",
        extra={
            "path": request.url.path,
            "query_keys": sorted(request.query_params.keys()),
            "hub_mode": hub_mode,
            "hub_challenge_present": bool(hub_challenge),
            "verify_token_masked": _mask_token(hub_verify_token),
        },
    )

    if hub_mode != "subscribe" or not hub_verify_token or not hub_challenge:
        logger.warning(
            "meta_webhook_verify_invalid_params",
            extra={
                "path": request.url.path,
                "hub_mode": hub_mode,
                "hub_challenge_present": bool(hub_challenge),
                "verify_token_masked": _mask_token(hub_verify_token),
                "status_code": 403,
            },
        )
        raise HTTPException(status_code=403, detail="Verificação inválida.")

    env_verify_token = settings.META_VERIFY_TOKEN.strip()
    if env_verify_token:
        is_valid = hub_verify_token == env_verify_token
        logger.info(
            "meta_webhook_verify_env_check",
            extra={
                "path": request.url.path,
                "env_token_configured": True,
                "verify_token_masked": _mask_token(hub_verify_token),
                "status_code": 200 if is_valid else 403,
            },
        )
        if not is_valid:
            raise HTTPException(status_code=403, detail="verify_token inválido.")
        logger.info(
            "meta_webhook_verify_success",
            extra={
                "path": request.url.path,
                "verification_source": "env",
                "status_code": 200,
            },
        )
        return PlainTextResponse(content=hub_challenge, status_code=200)

    logger.warning(
        "meta_webhook_verify_env_missing_fallback_integration",
        extra={
            "path": request.url.path,
            "verify_token_masked": _mask_token(hub_verify_token),
        },
    )

    integration = resolve_meta_verify_token(supa, verify_token=hub_verify_token)
    if not integration:
        logger.warning(
            "meta_webhook_verify_failed",
            extra={
                "path": request.url.path,
                "verify_token_masked": _mask_token(hub_verify_token),
                "verification_source": "integration_fallback",
                "status_code": 403,
            },
        )
        raise HTTPException(status_code=403, detail="verify_token inválido.")

    logger.info(
        "meta_webhook_verify_success",
        extra={
            "path": request.url.path,
            "verification_source": "integration_fallback",
            "integration_id": integration.get("id"),
            "status_code": 200,
        },
    )
    return PlainTextResponse(content=hub_challenge, status_code=200)


@router.post("/api/public/webhooks/meta/leadgen")
@router.post("/api/public/webhooks/meta/leadgen/", include_in_schema=False)
async def receive_meta_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(default=None, alias="X-Hub-Signature-256"),
    supa: Client = Depends(get_supabase_admin),
):
    raw_body = await request.body()
    try:
        _validate_meta_signature(
            body=raw_body,
            signature_header=x_hub_signature_256,
        )
    except HTTPException as exc:
        logger.warning(
            "meta_webhook_post_invalid_signature",
            extra={
                "path": request.url.path,
                "signature_present": bool(x_hub_signature_256),
                "status_code": exc.status_code,
            },
        )
        raise
    body = await request.json()
    logger.info(
        "meta_webhook_post_received",
        extra={
            "path": request.url.path,
            "object_type": body.get("object"),
            "entry_count": len(body.get("entry") or []),
            "signature_present": bool(x_hub_signature_256),
        },
    )
    object_type = body.get("object")
    processed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if object_type != "page":
        logger.info(
            "meta_webhook_post_ignored",
            extra={
                "path": request.url.path,
                "object_type": object_type,
                "status_code": 200,
            },
        )
        return {"ok": True, "processed": 0, "ignored": True}

    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "leadgen":
                continue

            payload = {
                **(change.get("value") or {}),
                "entry_id": entry.get("id"),
                "entry_time": entry.get("time"),
            }
            try:
                result = ingest_meta_lead_event(supa, payload=payload)
                processed.append(result)
            except HTTPException as exc:
                logger.warning(
                    "meta_webhook_post_process_error",
                    extra={
                        "path": request.url.path,
                        "page_id": payload.get("page_id"),
                        "form_id": payload.get("form_id"),
                        "leadgen_id": payload.get("leadgen_id"),
                        "status_code": exc.status_code,
                    },
                )
                errors.append(
                    {
                        "page_id": payload.get("page_id"),
                        "form_id": payload.get("form_id"),
                        "leadgen_id": payload.get("leadgen_id"),
                        "detail": exc.detail,
                    }
                )

    logger.info(
        "meta_webhook_post_completed",
        extra={
            "path": request.url.path,
            "processed_count": len(processed),
            "error_count": len(errors),
            "status_code": 200,
        },
    )
    return {
        "ok": True,
        "processed": len(processed),
        "errors": errors,
    }


@router.get("/meta/integrations", response_model=list[MetaIntegrationOut])
def list_meta_integrations(
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    resp = (
        _integration_provider_filter(
            supa.table("meta_lead_integrations")
            .select("*")
            .eq("org_id", ctx.org_id)
        )
        .order("ativo", desc=True)
        .order("created_at", desc=True)
        .execute()
    )
    rows = [
        row
        for row in (_safe_data(resp) or [])
        if not _is_active_oauth_draft(row) or _is_active_oauth_draft(row, user_id=ctx.user_id)
    ]
    return [_sanitize_integration(row) for row in rows]


@router.get("/meta/oauth/start", response_model=MetaOAuthStartOut)
def start_meta_oauth(
    ctx: AuthContext = Depends(require_manager),
):
    try:
        auth_url = build_meta_oauth_authorize_url(org_id=ctx.org_id, user_id=ctx.user_id)
    except HTTPException as exc:
        logger.warning(
            "meta_oauth_start_failed",
            extra={
                "org_id": ctx.org_id,
                "user_id": ctx.user_id,
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        )
        raise
    return {"ok": True, "auth_url": auth_url}


@router.get("/meta/oauth/callback")
def meta_oauth_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
    supa: Client = Depends(get_supabase_admin),
):
    state_validado = False
    token_exchange_ok = False
    pages_count = 0
    oauth_session_saved = False
    logger.info(
        "meta_oauth_callback_received",
        extra={
            "code_masked": _mask_token(code),
            "has_state": bool(state),
            "error": error,
        },
    )
    if error:
        return _build_frontend_redirect_response(
            params={
                "tab": "oauth",
                "error": error_description or error,
            },
            log_event="meta_oauth_callback_redirect_error",
            level="warning",
        )
    if not code or not state:
        return _build_frontend_redirect_response(
            params={"tab": "oauth", "error": "Callback OAuth Meta inválido."},
            log_event="meta_oauth_callback_redirect_invalid",
            level="warning",
        )

    try:
        parsed_state = parse_meta_oauth_state(state)
        state_validado = True
        logger.info(
            "meta_oauth_callback_state_validated: state_validado=true",
            extra={
                "org_id": parsed_state["org_id"],
                "user_id": parsed_state["user_id"],
                "code_masked": _mask_token(code),
                "state_validado": True,
            },
        )
        _ensure_callback_user_in_org(
            supa,
            org_id=parsed_state["org_id"],
            user_id=parsed_state["user_id"],
        )
        logger.info(
            "meta_oauth_callback_org_membership_validated",
            extra={
                "org_id": parsed_state["org_id"],
                "user_id": parsed_state["user_id"],
            },
        )
        user_access_token = exchange_meta_oauth_code(code=code)
        token_exchange_ok = True
        logger.info(
            "meta_oauth_callback_token_received: token_exchange_ok=true",
            extra={
                "org_id": parsed_state["org_id"],
                "user_id": parsed_state["user_id"],
                "access_token_masked": _mask_token(user_access_token),
                "token_exchange_ok": True,
            },
        )
        logger.info(
            "meta_oauth_callback_fetching_pages",
            extra={
                "org_id": parsed_state["org_id"],
                "user_id": parsed_state["user_id"],
            },
        )
        pages = list_meta_oauth_pages(user_access_token=user_access_token)
        pages_count = len(pages)
        logger.info(
            f"meta_oauth_callback_pages_loaded: pages_count={pages_count}",
            extra={
                "org_id": parsed_state["org_id"],
                "user_id": parsed_state["user_id"],
                "pages_count": pages_count,
                "pages": [
                    {
                        "page_id": page["id"],
                        "page_name": page.get("name"),
                        "access_token_masked": _mask_token(page.get("access_token")),
                    }
                    for page in pages
                ],
            },
        )
        if not pages:
            diagnostics = get_meta_oauth_user_diagnostics(
                user_access_token=user_access_token,
            )
            logger.warning(
                (
                    "meta_oauth_callback_no_pages: "
                    f"meta_user={((diagnostics.get('user') or {}).get('id') or '<unknown>')} "
                    f"granted={','.join(diagnostics.get('granted_permissions') or []) or '<none>'} "
                    f"declined={','.join(diagnostics.get('declined_permissions') or []) or '<none>'}"
                ),
                extra={
                    "org_id": parsed_state["org_id"],
                    "user_id": parsed_state["user_id"],
                    "pages_count": 0,
                    "granted_permissions": diagnostics.get("granted_permissions") or [],
                    "declined_permissions": diagnostics.get("declined_permissions") or [],
                    "meta_user": diagnostics.get("user"),
                    "permissions_error": diagnostics.get("permissions_error"),
                    "user_error": diagnostics.get("user_error"),
                },
            )
            raise HTTPException(
                status_code=404,
                detail=(
                    "Nenhuma página encontrada para este usuário. "
                    "Confirme se a conta Meta tem páginas acessíveis e se as permissões "
                    "`pages_show_list` e `pages_read_engagement` foram concedidas."
                ),
            )
        logger.info(
            "meta_oauth_callback_persisting_drafts",
            extra={
                "org_id": parsed_state["org_id"],
                "user_id": parsed_state["user_id"],
                "pages_count": len(pages),
                "page_ids": [page["id"] for page in pages],
            },
        )
        persisted_rows = save_meta_oauth_draft(
            supa,
            org_id=parsed_state["org_id"],
            user_id=parsed_state["user_id"],
            user_access_token=user_access_token,
            pages=pages,
        )
        oauth_session_saved = bool(persisted_rows)
        logger.info(
            f"meta_oauth_callback_db_persisted: oauth_session_saved={str(oauth_session_saved).lower()}",
            extra={
                "org_id": parsed_state["org_id"],
                "user_id": parsed_state["user_id"],
                "insert_count": len(persisted_rows),
                "integration_ids": [row["id"] for row in persisted_rows],
                "page_ids": [row["page_id"] for row in persisted_rows],
                "oauth_session_saved": oauth_session_saved,
            },
        )
        insert_audit_log(
            supa,
            org_id=parsed_state["org_id"],
            actor_id=parsed_state["user_id"],
            entity="meta_lead_integration",
            entity_id=persisted_rows[0]["id"],
            action="oauth_callback",
            diff={
                "pages_available": len(pages),
                "page_ids": [row["page_id"] for row in persisted_rows],
            },
        )
        return _build_frontend_redirect_response(
            params={"success": "true", "meta_connected": "1"},
            log_event="meta_oauth_callback_redirect_success",
            message_detail=(
                f"state_validado={str(state_validado).lower()} "
                f"token_exchange_ok={str(token_exchange_ok).lower()} "
                f"pages_count={pages_count} "
                f"oauth_session_saved={str(oauth_session_saved).lower()}"
            ),
        )
    except Exception as exc:
        error_message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        logger.exception(
            (
                f"meta_oauth_callback_failed: {error_message} "
                f"state_validado={str(state_validado).lower()} "
                f"token_exchange_ok={str(token_exchange_ok).lower()} "
                f"pages_count={pages_count} "
                f"oauth_session_saved={str(oauth_session_saved).lower()}"
            ),
            extra={
                "code_masked": _mask_token(code),
                "has_state": bool(state),
                "error_type": exc.__class__.__name__,
                "detail": error_message,
                "error_message": error_message,
                "error_repr": repr(exc),
                "status_code": exc.status_code if isinstance(exc, HTTPException) else 500,
                "state_validado": state_validado,
                "token_exchange_ok": token_exchange_ok,
                "pages_count": pages_count,
                "oauth_session_saved": oauth_session_saved,
            },
        )
        return _build_frontend_redirect_response(
            params={"tab": "oauth", "error": str(error_message)},
            log_event="meta_oauth_callback_redirect_error_result",
            level="warning",
            message_detail=(
                f"{error_message} "
                f"state_validado={str(state_validado).lower()} "
                f"token_exchange_ok={str(token_exchange_ok).lower()} "
                f"pages_count={pages_count} "
                f"oauth_session_saved={str(oauth_session_saved).lower()}"
            ),
        )


@router.post("/meta/integrations", response_model=MetaIntegrationOut, status_code=201)
def create_meta_integration(
    body: MetaIntegrationCreateIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    default_owner_id = _ensure_owner_in_org(
        supa,
        org_id=ctx.org_id,
        owner_id=body.default_owner_id,
    )

    payload = {
        "id": str(uuid4()),
        "org_id": ctx.org_id,
        "created_by": ctx.user_id,
        "updated_by": ctx.user_id,
        "nome": body.nome,
        "provider": META_PROVIDER,
        "page_id": body.page_id,
        "page_name": body.page_name,
        "form_id": body.form_id,
        "form_name": body.form_name,
        "source_label": body.source_label,
        "channel": META_CHANNEL,
        "default_owner_id": default_owner_id,
        "verify_token": body.verify_token,
        "access_token_encrypted": body.access_token,
        "ativo": body.ativo,
        "settings": body.settings or {},
        "updated_at": utcnow_iso(),
    }

    supa.table("meta_lead_integrations").insert(payload).execute()
    row = _get_integration_or_404(
        supa,
        org_id=ctx.org_id,
        integration_id=payload["id"],
    )
    if not row:
        raise HTTPException(status_code=500, detail="Erro ao criar integração Meta.")

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="meta_lead_integration",
        entity_id=row["id"],
        action="create",
        diff={
            "page_id": row["page_id"],
            "form_id": row.get("form_id"),
            "source_label": row["source_label"],
        },
    )
    return _sanitize_integration(row)


@router.get("/meta/pages", response_model=list[MetaPageOut])
def list_meta_oauth_pages_endpoint(
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return get_meta_oauth_pages_for_user(
        supa,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
    )


@router.get("/meta/pages/{page_id}/forms", response_model=list[MetaPageFormOut])
def list_meta_oauth_page_forms_endpoint(
    page_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return get_meta_page_forms_for_user(
        supa,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        page_id=page_id,
    )


@router.patch("/meta/integrations/{integration_id}", response_model=MetaIntegrationOut)
def patch_meta_integration(
    integration_id: str,
    body: MetaIntegrationUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    current = _get_integration_or_404(
        supa,
        org_id=ctx.org_id,
        integration_id=integration_id,
    )

    owner_id = _ensure_owner_in_org(
        supa,
        org_id=ctx.org_id,
        owner_id=body.default_owner_id if "default_owner_id" in body.model_fields_set else current.get("default_owner_id"),
    )

    payload: dict[str, Any] = {"updated_by": ctx.user_id, "updated_at": utcnow_iso()}
    for field in ("nome", "page_id", "page_name", "form_id", "form_name", "source_label", "ativo"):
        if field in body.model_fields_set:
            payload[field] = getattr(body, field)

    if "default_owner_id" in body.model_fields_set:
        payload["default_owner_id"] = owner_id
    if "verify_token" in body.model_fields_set and body.verify_token:
        payload["verify_token"] = body.verify_token
    if "access_token" in body.model_fields_set and body.access_token:
        payload["access_token_encrypted"] = body.access_token
    if "settings" in body.model_fields_set:
        payload["settings"] = body.settings or {}

    (
        supa.table("meta_lead_integrations")
        .update(payload)
        .eq("id", integration_id)
        .eq("org_id", ctx.org_id)
        .execute()
    )
    row = _get_integration_or_404(
        supa,
        org_id=ctx.org_id,
        integration_id=integration_id,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Erro ao atualizar integração Meta.")

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="meta_lead_integration",
        entity_id=row["id"],
        action="update",
        diff={
            "updated_fields": sorted(payload.keys()),
            "page_id": row["page_id"],
            "form_id": row.get("form_id"),
        },
    )
    return _sanitize_integration(row)


@router.post("/meta/integrations/from-oauth", response_model=MetaIntegrationOut)
def create_meta_integration_from_oauth(
    body: MetaOAuthFinalizeIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    default_owner_id = _ensure_owner_in_org(
        supa,
        org_id=ctx.org_id,
        owner_id=body.default_owner_id,
    )
    row = finalize_meta_oauth_integration(
        supa,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        nome=body.nome,
        source_label=body.source_label,
        page_id=body.page_id,
        form_id=body.form_id,
        default_owner_id=default_owner_id,
        ativo=body.ativo,
    )
    subscribed = False
    try:
        subscribe_result = subscribe_meta_page(supa, integration=row)
        subscribed = bool(subscribe_result.get("subscribed"))
    except Exception as exc:
        logger.warning(
            "meta_oauth_finalize_subscription_error",
            extra={
                "integration_id": row["id"],
                "page_id": row["page_id"],
                "status_code": 500,
                "message": str(exc),
            },
        )
    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="meta_lead_integration",
        entity_id=row["id"],
        action="oauth_finalize",
        diff={
            "page_id": row["page_id"],
            "form_id": row.get("form_id"),
            "subscribed": subscribed,
        },
    )
    return _sanitize_integration(row)


@router.get(
    "/meta/integrations/{integration_id}/events",
    response_model=list[MetaWebhookEventOut],
)
def list_meta_integration_events(
    integration_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    _get_integration_or_404(
        supa,
        org_id=ctx.org_id,
        integration_id=integration_id,
    )

    resp = (
        supa.table("meta_webhook_events")
        .select("*")
        .eq("org_id", ctx.org_id)
        .eq("integration_id", integration_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = _safe_data(resp) or []
    return rows


@router.post(
    "/meta/integrations/{integration_id}/subscribe-page",
    response_model=MetaSubscribePageOut,
)
def subscribe_meta_integration_page(
    integration_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    integration = _get_integration_or_404(
        supa,
        org_id=ctx.org_id,
        integration_id=integration_id,
    )
    result = subscribe_meta_page(supa, integration=integration)
    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="meta_lead_integration",
        entity_id=integration_id,
        action="subscribe_page",
        diff={"page_id": integration["page_id"], "subscribed": result["subscribed"]},
    )
    return result


@router.get(
    "/meta/integrations/{integration_id}/subscription-status",
    response_model=MetaSubscriptionStatusOut,
)
def get_meta_integration_subscription_status(
    integration_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    integration = _get_integration_or_404(
        supa,
        org_id=ctx.org_id,
        integration_id=integration_id,
    )
    return get_meta_subscription_status(supa, integration=integration)


@router.get(
    "/meta/integrations/{integration_id}/forms",
    response_model=list[MetaPageFormOut],
)
def get_meta_integration_forms(
    integration_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    integration = _get_integration_or_404(
        supa,
        org_id=ctx.org_id,
        integration_id=integration_id,
    )
    return list_meta_page_forms(integration=integration)


@router.post(
    "/meta/integrations/{integration_id}/test-connection",
    response_model=MetaConnectionTestOut,
)
def post_meta_integration_test_connection(
    integration_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    integration = _get_integration_or_404(
        supa,
        org_id=ctx.org_id,
        integration_id=integration_id,
    )
    result = test_meta_integration_connection(supa, integration=integration)
    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="meta_lead_integration",
        entity_id=integration_id,
        action="test_connection",
        diff={"page_id": integration["page_id"], "ok": result["ok"]},
    )
    return result
