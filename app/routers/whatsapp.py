from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from supabase import Client

from app.core.config import settings
from app.deps import get_supabase_admin
from app.schemas.whatsapp import (
    WhatsappConnectIn,
    WhatsappDeleteOut,
    WhatsappDispatchOut,
    WhatsappIntegrationOut,
    WhatsappManualConnectIn,
    WhatsappAiToggleIn,
    WhatsappLeadIn,
    WhatsappOkOut,
    WhatsappReplyIn,
    WhatsappSignupConfigOut,
    WhatsappTemplateOut,
    WhatsappTemplateUpdateIn,
    WhatsappTestSendIn,
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
@router.post("/whatsapp/ai/toggle", response_model=Optional[WhatsappIntegrationOut])
def toggle_whatsapp_ai(
    payload: WhatsappAiToggleIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    return wa.set_ai_enabled(supa=supa, org_id=ctx.org_id, enabled=payload.enabled)


@router.post("/whatsapp/ai/reativar", response_model=WhatsappOkOut)
def reativar_ia(
    payload: WhatsappLeadIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    wa.reativar_ia_lead(supa=supa, org_id=ctx.org_id, lead_id=payload.lead_id)
    return {"ok": True}


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
# Fase 2 - Dispatcher (drenado pelo cron do Railway) + envio de teste
# --------------------------------------------------------------------------- #
@router.post("/whatsapp/dispatch", response_model=WhatsappDispatchOut)
def dispatch_whatsapp_queue(
    x_dispatch_secret: Optional[str] = Header(default=None, alias="X-Dispatch-Secret"),
    limit: int = Query(default=25, ge=1, le=100),
    supa: Client = Depends(get_supabase_admin),
):
    secret = settings.WHATSAPP_DISPATCH_SECRET.strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Dispatcher não configurado (WHATSAPP_DISPATCH_SECRET).")
    if (x_dispatch_secret or "").strip() != secret:
        raise HTTPException(status_code=403, detail="Segredo do dispatcher inválido.")
    return wa.process_outbound_queue(supa=supa, limit=limit)


@router.post("/whatsapp/followups/run")
def run_followups(
    x_dispatch_secret: Optional[str] = Header(default=None, alias="X-Dispatch-Secret"),
    limit: int = Query(default=50, ge=1, le=200),
    supa: Client = Depends(get_supabase_admin),
):
    """Roda a varredura de follow-up + lembretes (protegido pelo mesmo segredo do dispatcher)."""
    secret = settings.WHATSAPP_DISPATCH_SECRET.strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Dispatcher não configurado (WHATSAPP_DISPATCH_SECRET).")
    if (x_dispatch_secret or "").strip() != secret:
        raise HTTPException(status_code=403, detail="Segredo do dispatcher inválido.")
    from app.services import whatsapp_followup_service as fup

    return fup.run_sweeps(supa=supa, limit=limit)


@router.post("/whatsapp/reply", response_model=WhatsappOkOut)
def reply_whatsapp(
    payload: WhatsappReplyIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    try:
        wa.send_reply(supa=supa, org_id=ctx.org_id, lead_id=payload.lead_id, body=payload.body)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("whatsapp_reply_failed", extra={"org_id": ctx.org_id})
        raise HTTPException(status_code=502, detail=f"Falha ao enviar: {exc}")


@router.post("/whatsapp/test-send", response_model=WhatsappDispatchOut)
def test_send_whatsapp(
    payload: WhatsappTestSendIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
):
    """Envia um template de teste imediatamente para validar a conexão."""
    integration = wa.get_integration_row(supa=supa, org_id=ctx.org_id)
    if not integration or not integration.get("ativo"):
        raise HTTPException(status_code=400, detail="Conecte o WhatsApp antes de enviar um teste.")
    try:
        wa.send_now(supa=supa, org_id=ctx.org_id, to=payload.to)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("whatsapp_test_send_failed", extra={"org_id": ctx.org_id})
        raise HTTPException(status_code=502, detail=f"Falha ao enviar teste: {exc}")
    return {"processed": 1, "sent": 1, "failed": 0, "skipped": 0}


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


def _valid_signature(raw: bytes, signature: Optional[str]) -> bool:
    """Valida X-Hub-Signature-256 com o app secret (quando configurado)."""
    secret = settings.WHATSAPP_APP_SECRET.strip()
    if not secret:
        # sem secret configurado, não bloqueia (verificação por token já ocorre no GET)
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.split("=", 1)[1])


@router.post("/api/public/webhooks/whatsapp")
@router.post("/api/public/webhooks/whatsapp/", include_in_schema=False)
async def receive_whatsapp_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(default=None, alias="X-Hub-Signature-256"),
    supa: Client = Depends(get_supabase_admin),
):
    raw = await request.body()
    if not _valid_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Assinatura inválida.")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = None

    if isinstance(payload, dict):
        try:
            stats = wa.handle_webhook_payload(supa=supa, payload=payload)
            logger.info("whatsapp_webhook_processed", extra={"stats": stats})
        except Exception:  # noqa: BLE001 - sempre responder 200 para a Meta não reenviar em loop
            logger.exception("whatsapp_webhook_error")

    # Sempre 200: a Meta reenvia em caso de erro/timeout.
    return {"received": True}
