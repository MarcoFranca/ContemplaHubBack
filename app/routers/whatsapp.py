from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from supabase import Client

from app.core.config import settings
from app.deps import get_supabase_admin
from app.schemas.whatsapp import (
    WhatsappConnectIn,
    WhatsappDeleteOut,
    WhatsappIntegrationOut,
    WhatsappManualConnectIn,
    WhatsappSignupConfigOut,
    WhatsappTemplateOut,
    WhatsappTemplateUpdateIn,
)
from app.security.auth import AuthContext
from app.security.permissions import require_manager
from app.services import whatsapp_service as wa

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])


# --------------------------------------------------------------------------- #
# Configuração / conexão
# --------------------------------------------------------------------------- #
@router.get("/whatsapp/config", response_model=WhatsappSignupConfigOut)
def get_whatsapp_config(
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    row = wa.get_integration_row(supa=supa, org_id=ctx.org_id)
    connected = bool(row and row.get("ativo"))
    return wa.signup_config(connected=connected)


@router.get("/whatsapp/integration", response_model=Optional[WhatsappIntegrationOut])
def get_whatsapp_integration(
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    row = wa.get_integration_row(supa=supa, org_id=ctx.org_id)
    return wa.sanitize_integration_or_none(row)


@router.post("/whatsapp/connect", response_model=WhatsappIntegrationOut)
def connect_whatsapp(
    payload: WhatsappConnectIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    try:
        return wa.connect_integration(
            supa=supa,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            code=payload.code,
            waba_id=payload.waba_id,
            phone_number_id=payload.phone_number_id,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("whatsapp_connect_failed", extra={"org_id": ctx.org_id})
        raise HTTPException(status_code=502, detail=f"Falha ao conectar WhatsApp: {exc}")


@router.post("/whatsapp/connect-manual", response_model=WhatsappIntegrationOut)
def connect_whatsapp_manual(
    payload: WhatsappManualConnectIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    try:
        return wa.connect_integration_manual(
            supa=supa,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            access_token=payload.access_token,
            waba_id=payload.waba_id,
            phone_number_id=payload.phone_number_id,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("whatsapp_connect_manual_failed", extra={"org_id": ctx.org_id})
        raise HTTPException(status_code=502, detail=f"Falha ao conectar WhatsApp: {exc}")


@router.delete("/whatsapp/integration/{integration_id}", response_model=WhatsappDeleteOut)
def disconnect_whatsapp(
    integration_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    row = wa.get_integration_row(supa=supa, org_id=ctx.org_id)
    if not row or str(row.get("id")) != str(integration_id):
        raise HTTPException(status_code=404, detail="Integração não encontrada.")
    wa.deactivate_integration(supa=supa, org_id=ctx.org_id, integration_id=integration_id)
    return {"ok": True, "id": integration_id}


# --------------------------------------------------------------------------- #
# Template configurável
# --------------------------------------------------------------------------- #
@router.get("/whatsapp/template", response_model=WhatsappTemplateOut)
def get_whatsapp_template(
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return wa.get_template(supa=supa, org_id=ctx.org_id, user_id=ctx.user_id)


@router.put("/whatsapp/template", response_model=WhatsappTemplateOut)
def put_whatsapp_template(
    payload: WhatsappTemplateUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return wa.update_template(
        supa=supa,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        changes=payload.model_dump(exclude_unset=True),
    )


# --------------------------------------------------------------------------- #
# Webhook público (verificação agora; recebimento completo na Fase 3)
# --------------------------------------------------------------------------- #
@router.get("/api/public/webhooks/whatsapp")
@router.get("/api/public/webhooks/whatsapp/", include_in_schema=False)
async def verify_whatsapp_webhook(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    env_token = settings.WHATSAPP_VERIFY_TOKEN.strip()
    if (
        hub_mode != "subscribe"
        or not hub_challenge
        or not env_token
        or hub_verify_token != env_token
    ):
        raise HTTPException(status_code=403, detail="Verificação inválida.")
    return PlainTextResponse(content=hub_challenge, status_code=200)


@router.post("/api/public/webhooks/whatsapp")
@router.post("/api/public/webhooks/whatsapp/", include_in_schema=False)
async def receive_whatsapp_webhook(request: Request):
    # Fase 3 tratará inbound e status. Por ora, apenas reconhecemos com 200
    # para a assinatura do webhook na Meta ficar válida.
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    logger.info(
        "whatsapp_webhook_received",
        extra={"has_body": bool(body)},
    )
    return {"received": True}
