from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.meta import (
    MetaIntegrationCreateIn,
    MetaIntegrationOut,
    MetaIntegrationUpdateIn,
    MetaWebhookEventOut,
)
from app.security.auth import AuthContext
from app.security.permissions import require_manager
from app.services.meta_leads_service import (
    META_CHANNEL,
    META_PROVIDER,
    _ensure_owner_in_org,
    _integration_provider_filter,
    _safe_data,
    ingest_meta_lead_event,
    insert_audit_log,
    resolve_meta_verify_token,
    utcnow_iso,
)


router = APIRouter(tags=["meta"])


def _sanitize_integration(row: dict[str, Any]) -> dict[str, Any]:
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


@router.get("/api/public/webhooks/meta/leadgen")
def verify_meta_webhook(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
    supa: Client = Depends(get_supabase_admin),
):
    if hub_mode != "subscribe" or not hub_verify_token or not hub_challenge:
        raise HTTPException(status_code=400, detail="Parâmetros de verificação inválidos.")

    integration = resolve_meta_verify_token(
        supa,
        verify_token=hub_verify_token,
    )
    if not integration:
        raise HTTPException(status_code=403, detail="verify_token inválido.")

    return PlainTextResponse(hub_challenge)


@router.post("/api/public/webhooks/meta/leadgen")
async def receive_meta_webhook(
    request: Request,
    supa: Client = Depends(get_supabase_admin),
):
    body = await request.json()
    object_type = body.get("object")
    processed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if object_type != "page":
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
                errors.append(
                    {
                        "page_id": payload.get("page_id"),
                        "form_id": payload.get("form_id"),
                        "leadgen_id": payload.get("leadgen_id"),
                        "detail": exc.detail,
                    }
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
    rows = _safe_data(resp) or []
    return [_sanitize_integration(row) for row in rows]


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

    resp = (
        supa.table("meta_lead_integrations")
        .insert(payload)
        .select("*")
        .single()
        .execute()
    )
    row = _safe_data(resp)
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

    resp = (
        supa.table("meta_lead_integrations")
        .update(payload)
        .eq("id", integration_id)
        .eq("org_id", ctx.org_id)
        .select("*")
        .single()
        .execute()
    )
    row = _safe_data(resp)
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
