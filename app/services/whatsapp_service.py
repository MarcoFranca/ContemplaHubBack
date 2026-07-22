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
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

import requests
from fastapi import HTTPException, status
from supabase import Client

from app.core.config import settings
from app.services.whatsapp_quick_replies import extract_quick_replies

logger = logging.getLogger(__name__)

WHATSAPP_PROVIDER = "meta_cloud"
DEFAULT_TEMPLATE_KEY = "lead_welcome"
DEFAULT_TEMPLATE_BODY = (
    "Olá {{1}}! Recebemos seu contato e um especialista já vai falar com você. "
    "Enquanto isso, pode nos contar qual seu objetivo com o consórcio?"
)


def _normalize_operational_payload(payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    data = dict(payload or {})
    if data.get("followup"):
        source = "followup"
    elif data.get("reminder"):
        source = "lembrete"
    elif data.get("auto_reply"):
        source = "fallback_boas_vindas"
    elif data.get("ai_fallback"):
        source = "fallback_erro_ia"
    elif data.get("ai_media_fallback"):
        source = "fallback_midia"
    elif data.get("manual_reply"):
        source = "manual"
    elif data.get("ai"):
        source = "ia"
    else:
        source = "operacional"

    if data.get("ai_handoff"):
        data["operational_handoff"] = True

    data["operational_source"] = source
    return data


def _describe_operational_source(payload: Optional[dict[str, Any]]) -> str:
    data = payload or {}
    source = data.get("operational_source")
    if source == "ia":
        return "IA normal"
    if source == "fallback_boas_vindas":
        return "Fallback de boas-vindas"
    if source == "fallback_erro_ia":
        return "Fallback por falha da IA"
    if source == "fallback_midia":
        return "Fallback para mídia não suportada"
    if source == "followup":
        return "Follow-up automático"
    if source == "lembrete":
        return "Lembrete automático"
    if source == "manual":
        return "Resposta manual"
    return "Operacional"


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
    # Não levanta erro se faltar credencial: a conexão manual não precisa do
    # app secret, e a página de config deve carregar mesmo sem Embedded Signup.
    return {
        "ok": True,
        "app_id": settings.WHATSAPP_APP_ID.strip(),
        "config_id": settings.WHATSAPP_ES_CONFIG_ID.strip(),
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


def set_ai_enabled(*, supa: Client, org_id: str, enabled: bool) -> Optional[dict[str, Any]]:
    supa.table("whatsapp_integrations").update({"ai_enabled": enabled}).eq("org_id", org_id).execute()
    return sanitize_integration_or_none(get_integration_row(supa=supa, org_id=org_id))


def reativar_ia_lead(*, supa: Client, org_id: str, lead_id: str) -> dict[str, Any]:
    """Limpa o marcador de handoff do lead: a IA volta a atender aquela conversa."""
    resp = (
        supa.table("whatsapp_messages")
        .select("id, payload")
        .eq("org_id", org_id)
        .eq("lead_id", lead_id)
        .filter("payload->>ai_handoff", "eq", "true")
        .execute()
    )
    n = 0
    for row in getattr(resp, "data", None) or []:
        payload = row.get("payload")
        if isinstance(payload, dict):
            payload["ai_handoff"] = False
            supa.table("whatsapp_messages").update({"payload": payload}).eq("id", row["id"]).execute()
            n += 1
    return {"ok": True, "limpos": n}


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


# --------------------------------------------------------------------------- #
# Fase 2 - Dispatcher: drena whatsapp_outbound_queue e envia via Cloud API
# --------------------------------------------------------------------------- #
# backoff por tentativa (minutos): 1, 5, 15, 60, 180
_RETRY_BACKOFF_MIN = [1, 5, 15, 60, 180]


def normalize_msisdn(phone: str) -> str:
    """Normaliza para número internacional só com dígitos (DDI 55 para BR)."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    if len(digits) in (10, 11):
        return "55" + digits
    return digits


def _build_template_payload(*, to: str, template: dict[str, Any], nome: Optional[str]) -> dict[str, Any]:
    """Monta o payload de template. Sem template aprovado configurado, usa hello_world."""
    template_name = _trim(template.get("template_name"))
    language = _trim(template.get("language")) or "pt_BR"
    variables = template.get("variables") or []

    if not template_name:
        # fallback para validação em número de teste (sem variáveis)
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {"name": "hello_world", "language": {"code": "en_US"}},
        }

    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {"name": template_name, "language": {"code": language}},
    }

    if variables:
        values = {"nome": nome or "cliente"}
        parameters = [{"type": "text", "text": str(values.get(v, "")) or " "} for v in variables]
        payload["template"]["components"] = [{"type": "body", "parameters": parameters}]

    return payload


def send_template_message(
    *, access_token: str, phone_number_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    resp = requests.post(
        f"{_graph_base()}/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        json=payload,
        timeout=20,
    )
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        raise RuntimeError(f"WhatsApp send falhou: {resp.status_code} {resp.text}")
    return data if isinstance(data, dict) else {}


def _fetch_lead_nome(*, supa: Client, lead_id: Optional[str]) -> Optional[str]:
    if not lead_id:
        return None
    resp = supa.table("leads").select("nome").eq("id", lead_id).limit(1).execute()
    rows = getattr(resp, "data", None) or []
    return (rows[0].get("nome") if rows else None)


def _mark_queue(supa: Client, item_id: str, changes: dict[str, Any]) -> None:
    changes = {**changes, "updated_at": datetime.now(timezone.utc).isoformat()}
    supa.table("whatsapp_outbound_queue").update(changes).eq("id", item_id).execute()


def _schedule_retry(supa: Client, item: dict[str, Any], error: str) -> None:
    attempts = int(item.get("attempts") or 0) + 1
    max_attempts = int(item.get("max_attempts") or 5)
    if attempts >= max_attempts:
        _mark_queue(supa, item["id"], {"status": "failed", "attempts": attempts, "last_error": error})
        return
    backoff = _RETRY_BACKOFF_MIN[min(attempts - 1, len(_RETRY_BACKOFF_MIN) - 1)]
    next_at = datetime.now(timezone.utc) + timedelta(minutes=backoff)
    _mark_queue(
        supa,
        item["id"],
        {
            "status": "pending",
            "attempts": attempts,
            "last_error": error,
            "next_attempt_at": next_at.isoformat(),
        },
    )


def send_now(*, supa: Client, org_id: str, to: str, lead_id: Optional[str] = None) -> dict[str, Any]:
    """Envio imediato (usado pelo botão de teste). Levanta em caso de falha."""
    integration = get_integration_row(supa=supa, org_id=org_id)
    if not integration or not integration.get("ativo"):
        raise HTTPException(status_code=400, detail="WhatsApp não conectado.")
    access_token = _trim(integration.get("access_token"))
    phone_number_id = _trim(integration.get("phone_number_id"))
    to_norm = normalize_msisdn(_trim(to))
    if not to_norm:
        raise HTTPException(status_code=400, detail="Telefone inválido.")

    template = get_template(supa=supa, org_id=org_id)
    nome = _fetch_lead_nome(supa=supa, lead_id=lead_id)
    payload = _build_template_payload(to=to_norm, template=template, nome=nome)
    result = send_template_message(
        access_token=access_token, phone_number_id=phone_number_id, payload=payload
    )
    wa_message_id = None
    messages = result.get("messages") if isinstance(result, dict) else None
    if isinstance(messages, list) and messages:
        wa_message_id = messages[0].get("id")
    supa.table("whatsapp_messages").insert(
        {
            "org_id": org_id,
            "lead_id": lead_id,
            "direction": "out",
            "wa_message_id": wa_message_id,
            "phone": to_norm,
            "msg_type": "template",
            "template_key": (template or {}).get("key"),
            "body": (template or {}).get("body_text"),
            "status": "sent",
            "payload": _normalize_operational_payload(payload),
        }
    ).execute()
    return result


def send_reply(*, supa: Client, org_id: str, lead_id: str, body: str) -> dict[str, Any]:
    """Resposta de texto livre a um lead (válida na janela de 24h). Loga a mensagem."""
    body = (body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")

    integration = get_integration_row(supa=supa, org_id=org_id)
    if not integration or not integration.get("ativo"):
        raise HTTPException(status_code=400, detail="WhatsApp não conectado.")

    lead_resp = (
        supa.table("leads").select("id, telefone").eq("org_id", org_id).eq("id", lead_id).limit(1).execute()
    )
    lead_rows = getattr(lead_resp, "data", None) or []
    if not lead_rows:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    to = normalize_msisdn(_trim(lead_rows[0].get("telefone")))
    if not to:
        # fallback: telefone da última mensagem recebida
        last_in = (
            supa.table("whatsapp_messages")
            .select("phone")
            .eq("org_id", org_id)
            .eq("lead_id", lead_id)
            .eq("direction", "in")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(last_in, "data", None) or []
        to = normalize_msisdn(_trim(rows[0].get("phone"))) if rows else ""
    if not to:
        raise HTTPException(status_code=400, detail="Lead sem telefone válido.")

    result = send_text_message(
        access_token=_trim(integration.get("access_token")),
        phone_number_id=_trim(integration.get("phone_number_id")),
        to=to,
        body=body,
    )
    wamid = None
    msgs = result.get("messages") if isinstance(result, dict) else None
    if isinstance(msgs, list) and msgs:
        wamid = msgs[0].get("id")

    supa.table("whatsapp_messages").insert(
        {
            "org_id": org_id,
            "lead_id": lead_id,
            "direction": "out",
            "wa_message_id": wamid,
            "phone": to,
            "msg_type": "text",
            "body": body,
            "status": "sent",
            "payload": _normalize_operational_payload({"manual_reply": True}),
        }
    ).execute()
    return {"ok": True, "wa_message_id": wamid}


def process_outbound_queue(*, supa: Client, limit: int = 25) -> dict[str, Any]:
    """Drena a fila: envia pendentes cuja hora chegou. Chamado pelo cron (Railway)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    resp = (
        supa.table("whatsapp_outbound_queue")
        .select("*")
        .eq("status", "pending")
        .lte("next_attempt_at", now_iso)
        .order("next_attempt_at", desc=False)
        .limit(limit)
        .execute()
    )
    items = getattr(resp, "data", None) or []

    sent = 0
    failed = 0
    skipped = 0

    for item in items:
        item_id = item["id"]
        org_id = item["org_id"]

        integration = get_integration_row(supa=supa, org_id=org_id)
        if not integration or not integration.get("ativo"):
            _mark_queue(supa, item_id, {"status": "skipped", "last_error": "sem integração ativa"})
            skipped += 1
            continue

        access_token = _trim(integration.get("access_token"))
        phone_number_id = _trim(integration.get("phone_number_id"))
        if not access_token or not phone_number_id:
            _mark_queue(supa, item_id, {"status": "skipped", "last_error": "integração sem token/numero"})
            skipped += 1
            continue

        to = normalize_msisdn(_trim(item.get("phone")))
        if not to:
            _mark_queue(supa, item_id, {"status": "failed", "last_error": "telefone inválido"})
            failed += 1
            continue

        # marca em processamento (reduz corrida entre execuções do cron)
        _mark_queue(supa, item_id, {"status": "processing"})

        template = get_template(supa=supa, org_id=org_id)
        nome = _fetch_lead_nome(supa=supa, lead_id=item.get("lead_id"))
        payload = _build_template_payload(to=to, template=template, nome=nome)

        try:
            result = send_template_message(
                access_token=access_token, phone_number_id=phone_number_id, payload=payload
            )
            wa_message_id = None
            messages = result.get("messages") if isinstance(result, dict) else None
            if isinstance(messages, list) and messages:
                wa_message_id = messages[0].get("id")

            msg = (
                supa.table("whatsapp_messages")
                .insert(
                    {
                        "org_id": org_id,
                        "lead_id": item.get("lead_id"),
                        "direction": "out",
                        "wa_message_id": wa_message_id,
                        "phone": to,
                        "msg_type": "template",
                        "template_key": item.get("template_key"),
                        "body": (template or {}).get("body_text"),
                        "status": "sent",
                        "payload": _normalize_operational_payload(payload),
                    }
                )
                .execute()
            )
            msg_rows = getattr(msg, "data", None) or []
            message_id = msg_rows[0]["id"] if msg_rows else None

            _mark_queue(
                supa,
                item_id,
                {
                    "status": "sent",
                    "attempts": int(item.get("attempts") or 0) + 1,
                    "message_id": message_id,
                    "last_error": None,
                },
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp_send_failed", extra={"org_id": org_id, "error": str(exc)})
            _schedule_retry(supa, item, str(exc))
            failed += 1

    return {"processed": len(items), "sent": sent, "failed": failed, "skipped": skipped}


# --------------------------------------------------------------------------- #
# Fase 3 - Inbound: recebe respostas/Click-to-WhatsApp, cria lead, auto-resposta
# --------------------------------------------------------------------------- #
def _resolve_integration_by_phone_number_id(
    supa: Client, phone_number_id: Optional[str]
) -> Optional[dict[str, Any]]:
    if not phone_number_id:
        return None
    resp = (
        supa.table("whatsapp_integrations")
        .select("*")
        .eq("phone_number_id", str(phone_number_id))
        .eq("ativo", True)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def send_text_message(*, access_token: str, phone_number_id: str, to: str, body: str) -> dict[str, Any]:
    """Mensagem de texto livre (só válida dentro da janela de 24h de atendimento)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    return send_template_message(access_token=access_token, phone_number_id=phone_number_id, payload=payload)


def send_typing_indicator(*, access_token: str, phone_number_id: str, message_id: str) -> None:
    """Marca a mensagem como lida e mostra 'digitando...' (best-effort)."""
    if not message_id:
        return
    try:
        requests.post(
            f"{_graph_base()}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
                "typing_indicator": {"type": "text"},
            },
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("whatsapp_typing_falhou", extra={"error": str(exc)})


def download_media(*, access_token: str, media_id: str) -> Optional[tuple[bytes, str]]:
    """Baixa a mídia (áudio/imagem) do WhatsApp. Retorna (bytes, mime) ou None."""
    try:
        meta = requests.get(
            f"{_graph_base()}/{media_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if meta.status_code >= 400:
            return None
        info = meta.json()
        url = info.get("url")
        mime = info.get("mime_type") or "application/octet-stream"
        if not url:
            return None
        binr = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
        if binr.status_code >= 400:
            return None
        return binr.content, mime
    except Exception as exc:  # noqa: BLE001
        logger.warning("whatsapp_download_media_falhou", extra={"error": str(exc)})
        return None


def upload_media(*, access_token: str, phone_number_id: str, data: bytes, mime: str, filename: str = "audio.ogg") -> Optional[str]:
    """Sobe uma mídia e retorna o media_id."""
    try:
        resp = requests.post(
            f"{_graph_base()}/{phone_number_id}/media",
            headers={"Authorization": f"Bearer {access_token}"},
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, data, mime)},
            timeout=60,
        )
        if resp.status_code >= 400:
            logger.warning("whatsapp_upload_media_falhou", extra={"status": resp.status_code, "body": resp.text[:300]})
            return None
        return (resp.json() or {}).get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("whatsapp_upload_media_erro", extra={"error": str(exc)})
        return None


def send_audio_message(*, access_token: str, phone_number_id: str, to: str, media_id: str) -> dict[str, Any]:
    """Envia uma mensagem de voz (áudio) já uploadada."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "audio",
        "audio": {"id": media_id},
    }
    return send_template_message(access_token=access_token, phone_number_id=phone_number_id, payload=payload)


def send_document_message(
    *, access_token: str, phone_number_id: str, to: str, media_id: str, filename: str, caption: Optional[str] = None
) -> dict[str, Any]:
    """Envia um documento (ex.: PDF) já uploadado."""
    doc: dict[str, Any] = {"id": media_id, "filename": filename}
    if caption:
        doc["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "document",
        "document": doc,
    }
    return send_template_message(access_token=access_token, phone_number_id=phone_number_id, payload=payload)


def send_interactive_button_message(
    *, access_token: str, phone_number_id: str, to: str, body: str, buttons: list[str]
) -> dict[str, Any]:
    """Envia ate tres respostas rapidas, conforme os limites da Cloud API."""
    options = [str(title).strip()[:20] for title in buttons if str(title).strip()][:3]
    if len(options) < 2:
        raise ValueError("Mensagem interativa exige pelo menos duas opcoes.")
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": (body or "Escolha uma opcao:").strip()[:1024]},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": f"quick_{uuid4().hex[:12]}_{index}", "title": title},
                    }
                    for index, title in enumerate(options, start=1)
                ]
            },
        },
    }
    return send_template_message(access_token=access_token, phone_number_id=phone_number_id, payload=payload)


def _wamid_exists(supa: Client, wamid: Optional[str]) -> bool:
    if not wamid:
        return False
    resp = supa.table("whatsapp_messages").select("id").eq("wa_message_id", wamid).limit(1).execute()
    return bool(getattr(resp, "data", None))


def _extract_message_text(msg: dict[str, Any]) -> Optional[str]:
    mtype = msg.get("type")
    if mtype == "text":
        return (msg.get("text") or {}).get("body")
    if mtype == "button":
        return (msg.get("button") or {}).get("text")
    if mtype == "interactive":
        inter = msg.get("interactive") or {}
        sub = inter.get(inter.get("type") or "", {}) if isinstance(inter, dict) else {}
        return sub.get("title") if isinstance(sub, dict) else None
    return f"[{mtype}]" if mtype else None


def _find_or_create_lead(
    supa: Client, org_id: str, phone: str, nome: Optional[str]
) -> tuple[Optional[dict[str, Any]], bool]:
    digits = re.sub(r"\D", "", phone or "")
    candidates = {digits}
    if digits.startswith("55") and len(digits) in (12, 13):
        candidates.add(digits[2:])  # sem DDI
    elif len(digits) in (10, 11):
        candidates.add("55" + digits)

    for cand in candidates:
        resp = (
            supa.table("leads")
            .select("id, nome, telefone")
            .eq("org_id", org_id)
            .eq("telefone", cand)
            .limit(1)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if rows:
            return rows[0], False

    lead_id = str(uuid4())
    payload = {
        "id": lead_id,
        "org_id": org_id,
        "nome": (nome or "").strip() or f"WhatsApp {digits[-4:]}" if digits else (nome or "Lead WhatsApp"),
        "telefone": digits or phone,
        "origem": "whatsapp",
        "etapa": "novo",
        "channel": "whatsapp",
    }
    supa.table("leads").insert(payload).execute()
    return {"id": lead_id, "nome": payload["nome"], "telefone": payload["telefone"]}, True


def _welcome_text(template: dict[str, Any], nome: Optional[str]) -> str:
    body = _trim(template.get("body_text")) or (
        "Olá {{1}}! Recebemos seu contato e um especialista já vai falar com você."
    )
    return body.replace("{{1}}", (nome or "").strip() or "tudo bem")


def _apply_status(supa: Client, st: dict[str, Any]) -> None:
    wamid = st.get("id")
    status_val = st.get("status")  # sent|delivered|read|failed
    if not wamid or not status_val:
        return
    changes: dict[str, Any] = {"status": status_val, "updated_at": datetime.now(timezone.utc).isoformat()}
    if status_val == "failed":
        errors = st.get("errors") or []
        if errors:
            changes["error"] = str(errors[0].get("title") or errors[0])
    supa.table("whatsapp_messages").update(changes).eq("wa_message_id", wamid).execute()


def _handle_inbound(
    supa: Client, integration: dict[str, Any], msg: dict[str, Any], contact_name: Optional[str]
) -> dict[str, Any]:
    org_id = integration["org_id"]
    wamid = msg.get("id")
    from_wa = msg.get("from")

    if _wamid_exists(supa, wamid):
        return {}

    mtype = msg.get("type")
    access_token = _trim(integration.get("access_token"))
    phone_number_id = _trim(integration.get("phone_number_id"))
    origem_audio = False
    text = _extract_message_text(msg)

    # Áudio recebido: transcreve para texto (se habilitado).
    if mtype == "audio" and settings.WHATSAPP_AUDIO_ENABLED and settings.OPENAI_API_KEY.strip():
        media_id = (msg.get("audio") or {}).get("id")
        if media_id:
            media = download_media(access_token=access_token, media_id=media_id)
            if media:
                from app.ai import audio as ai_audio

                transcript = ai_audio.transcrever(media[0], media[1])
                if transcript:
                    text = transcript
                    origem_audio = True

    lead, created = _find_or_create_lead(supa, org_id, from_wa or "", contact_name)
    lead_id = lead.get("id") if lead else None

    # Click-to-WhatsApp (anúncio): registra a origem do anúncio no lead novo.
    referral = msg.get("referral") if isinstance(msg, dict) else None
    if referral and lead_id and created:
        try:
            headline = (referral.get("headline") or referral.get("body") or "Anúncio WhatsApp").strip()
            patch: dict[str, Any] = {"source_label": headline[:200], "channel": "whatsapp_ad"}
            if referral.get("source_id"):
                patch["utm_content"] = str(referral["source_id"])[:200]
            supa.table("leads").update(patch).eq("org_id", org_id).eq("id", lead_id).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp_ctwa_enrich_falhou", extra={"org_id": org_id, "error": str(exc)})

    supa.table("whatsapp_messages").insert(
        {
            "org_id": org_id,
            "lead_id": lead_id,
            "direction": "in",
            "wa_message_id": wamid,
            "phone": from_wa,
            "msg_type": mtype,
            "body": text,
            "status": "received",
            "payload": msg,
        }
    ).execute()

    auto_replied = False
    ai_replied = False
    ai_failed = False
    ai_error: Optional[str] = None
    handoff_active = False

    # 1) Agente de IA (se ligado para a org e o lead não estiver em atendimento humano).
    ai_on = settings.WHATSAPP_AI_ENABLED and bool(integration.get("ai_enabled"))
    # processável: texto/botão/interactive OU áudio transcrito com sucesso.
    ia_processavel = mtype in ("text", "button", "interactive") or origem_audio
    if ai_on:
        try:
            from app.ai import agent as ai_agent

            handoff_active = ai_agent.lead_em_handoff(supa, org_id, lead_id)
            if handoff_active:
                logger.info(
                    "whatsapp_ai_skip_handoff",
                    extra={"org_id": org_id, "lead_id": lead_id, "wa_message_id": wamid},
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp_ai_handoff_check_failed", extra={"org_id": org_id, "error": str(exc)})
            handoff_active = False

    if ai_on and not ia_processavel:
        # imagem/documento/áudio não transcrito: pede por texto (educado).
        try:
            if not handoff_active:
                _send_and_log_reply(
                    supa=supa,
                    integration=integration,
                    org_id=org_id,
                    lead_id=lead_id,
                    to=from_wa,
                    body="Por enquanto consigo te atender melhor por texto. Pode me escrever sua dúvida ou o que você procura? 🙂",
                    payload={"ai": True, "ai_media_fallback": True},
                )
                ai_replied = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp_ai_media_fallback_failed", extra={"org_id": org_id, "error": str(exc)})
    elif ai_on:
        try:
            if not handoff_active:
                send_typing_indicator(access_token=access_token, phone_number_id=phone_number_id, message_id=wamid)
                history_limit = max(settings.WHATSAPP_AI_MAX_HISTORY * 6, 60)
                hist_resp = (
                    supa.table("whatsapp_messages")
                    .select("direction, body, msg_type, created_at, payload")
                    .eq("org_id", org_id)
                    .eq("lead_id", lead_id)
                    .order("created_at", desc=False)
                    .limit(history_limit)
                    .execute()
                )
                history = getattr(hist_resp, "data", None) or []
                result = ai_agent.run_agent(
                    supa=supa,
                    org_id=org_id,
                    lead_id=lead_id,
                    history=history,
                    nome_cliente=(lead.get("nome") if lead else None),
                )
                reply_text = result.get("reply")
                if reply_text:
                    _send_ai_reply(
                        supa=supa,
                        integration=integration,
                        org_id=org_id,
                        lead_id=lead_id,
                        to=from_wa,
                        text=reply_text,
                        as_audio=bool(origem_audio and settings.WHATSAPP_AUDIO_REPLY),
                        escalated=bool(result.get("escalated")),
                        handoff_reason=(result.get("handoff_reason") or None),
                        product_context=(result.get("product_context") or None),
                        nome_cliente=(lead.get("nome") if lead else None),
                    )
                    ai_replied = True
                else:
                    # rodou mas não produziu texto (erro de API/modelo ou loop sem resposta)
                    ai_failed = True
                    ai_error = result.get("erro") or "sem resposta do modelo"
                    logger.warning("whatsapp_ai_sem_resposta", extra={"org_id": org_id, "erro": ai_error})
        except Exception as exc:  # noqa: BLE001
            ai_failed = True
            ai_error = str(exc)
            logger.warning("whatsapp_ai_failed", extra={"org_id": org_id, "error": ai_error})

    # 2) Fallback: auto-resposta fixa só no primeiro contato quando a IA não respondeu.
    if not ai_replied and created:
        try:
            template = get_template(supa=supa, org_id=org_id)
            body = _welcome_text(template, lead.get("nome") if lead else None)
            _send_and_log_reply(
                supa=supa,
                integration=integration,
                org_id=org_id,
                lead_id=lead_id,
                to=from_wa,
                body=body,
                payload={"auto_reply": True},
            )
            auto_replied = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp_autoreply_failed", extra={"org_id": org_id, "error": str(exc)})

    # Registra a falha da IA para observabilidade (quando falhou, lead e por quê).
    if ai_failed:
        try:
            supa.table("ai_falhas").insert(
                {
                    "org_id": org_id,
                    "lead_id": lead_id,
                    "telefone": from_wa,
                    "contexto": "whatsapp_inbound",
                    "erro": (ai_error or "desconhecido")[:1000],
                }
            ).execute()
        except Exception:  # noqa: BLE001 - tabela pode não existir ainda; não pode quebrar o fluxo
            pass

    # 3) Rede de segurança: a IA tentou e falhou numa conversa em andamento.
    #    Em vez de deixar o cliente no silêncio, manda uma mensagem curta.
    if ai_failed and not ai_replied and not auto_replied:
        try:
            _send_and_log_reply(
                supa=supa,
                integration=integration,
                org_id=org_id,
                lead_id=lead_id,
                to=from_wa,
                body="Recebi sua mensagem! Só um instante que já te retorno. 🙏",
                payload={"ai_fallback": True},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp_ai_fallback_failed", extra={"org_id": org_id, "error": str(exc)})

    return {"lead_created": created, "auto_replied": auto_replied or ai_replied}


def _send_ai_reply(
    *,
    supa: Client,
    integration: dict[str, Any],
    org_id: str,
    lead_id: Optional[str],
    to: str,
    text: str,
    as_audio: bool,
    escalated: bool,
    handoff_reason: Optional[str],
    product_context: Optional[str] = None,
    nome_cliente: Optional[str] = None,
) -> None:
    """Envia a resposta da IA em áudio (se origem foi áudio) ou texto. Loga o texto."""
    base_payload = _normalize_operational_payload(
        {
            "ai": True,
            "ai_handoff": escalated,
            "handoff_reason": handoff_reason,
            "product": product_context,
        }
    )
    # Perguntas fechadas precisam permanecer visuais para que os botões sejam
    # clicáveis; nos demais casos preservamos a resposta em áudio.
    has_quick_replies = any(
        extract_quick_replies(part)[1]
        for part in (text or "").split("|||")
    )
    if as_audio and not has_quick_replies:
        try:
            from app.ai import audio as ai_audio

            voice = ai_audio.sintetizar(text, nome_cliente=nome_cliente)
            if voice:
                data, mime = voice
                media_id = upload_media(
                    access_token=_trim(integration.get("access_token")),
                    phone_number_id=_trim(integration.get("phone_number_id")),
                    data=data,
                    mime=mime,
                )
                if media_id:
                    reply = send_audio_message(
                        access_token=_trim(integration.get("access_token")),
                        phone_number_id=_trim(integration.get("phone_number_id")),
                        to=to,
                        media_id=media_id,
                    )
                    reply_wamid = None
                    reply_msgs = reply.get("messages") if isinstance(reply, dict) else None
                    if isinstance(reply_msgs, list) and reply_msgs:
                        reply_wamid = reply_msgs[0].get("id")
                    supa.table("whatsapp_messages").insert(
                        {
                            "org_id": org_id,
                            "lead_id": lead_id,
                            "direction": "out",
                            "wa_message_id": reply_wamid,
                            "phone": to,
                            "msg_type": "audio",
                            "body": text,  # texto da fala (fica legível no inbox)
                            "status": "sent",
                            "payload": {**base_payload, "audio": True},
                        }
                    ).execute()
                    return
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp_ai_audio_reply_falhou", extra={"org_id": org_id, "error": str(exc)})
        # se o áudio falhar, cai para texto abaixo

    # A IA pode separar a resposta em mensagens sequenciais com '|||'
    # (ex.: mandar a proposta e, em seguida, o convite para reunião).
    partes = [p.strip() for p in (text or "").split("|||") if p.strip()] or [text]
    for parte in partes[:4]:  # limite de segurança
        body, quick_replies = extract_quick_replies(parte)
        if quick_replies:
            try:
                _send_and_log_interactive_reply(
                    supa=supa,
                    integration=integration,
                    org_id=org_id,
                    lead_id=lead_id,
                    to=to,
                    body=body,
                    buttons=quick_replies,
                    payload=base_payload,
                )
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("whatsapp_ai_quick_reply_falhou", extra={"org_id": org_id, "error": str(exc)})
                body = f"{body}\n\nResponda com: {' / '.join(quick_replies)}"
        _send_and_log_reply(
            supa=supa, integration=integration, org_id=org_id, lead_id=lead_id, to=to, body=body, payload=base_payload
        )


def _send_and_log_interactive_reply(
    *,
    supa: Client,
    integration: dict[str, Any],
    org_id: str,
    lead_id: Optional[str],
    to: str,
    body: str,
    buttons: list[str],
    payload: dict[str, Any],
) -> None:
    normalized_payload = _normalize_operational_payload(
        {**payload, "quick_reply": True, "options": buttons}
    )
    reply = send_interactive_button_message(
        access_token=_trim(integration.get("access_token")),
        phone_number_id=_trim(integration.get("phone_number_id")),
        to=to,
        body=body,
        buttons=buttons,
    )
    reply_wamid = None
    reply_msgs = reply.get("messages") if isinstance(reply, dict) else None
    if isinstance(reply_msgs, list) and reply_msgs:
        reply_wamid = reply_msgs[0].get("id")
    supa.table("whatsapp_messages").insert(
        {
            "org_id": org_id,
            "lead_id": lead_id,
            "direction": "out",
            "wa_message_id": reply_wamid,
            "phone": to,
            "msg_type": "interactive",
            "body": body,
            "status": "sent",
            "payload": normalized_payload,
        }
    ).execute()


def _send_and_log_reply(
    *,
    supa: Client,
    integration: dict[str, Any],
    org_id: str,
    lead_id: Optional[str],
    to: str,
    body: str,
    payload: dict[str, Any],
) -> None:
    normalized_payload = _normalize_operational_payload(payload)
    reply = send_text_message(
        access_token=_trim(integration.get("access_token")),
        phone_number_id=_trim(integration.get("phone_number_id")),
        to=to,
        body=body,
    )
    reply_wamid = None
    reply_msgs = reply.get("messages") if isinstance(reply, dict) else None
    if isinstance(reply_msgs, list) and reply_msgs:
        reply_wamid = reply_msgs[0].get("id")
    supa.table("whatsapp_messages").insert(
        {
            "org_id": org_id,
            "lead_id": lead_id,
            "direction": "out",
            "wa_message_id": reply_wamid,
            "phone": to,
            "msg_type": "text",
            "body": body,
            "status": "sent",
            "payload": normalized_payload,
        }
    ).execute()


def handle_webhook_payload(*, supa: Client, payload: dict[str, Any]) -> dict[str, Any]:
    """Processa o payload do webhook do WhatsApp (mensagens + status)."""
    stats = {"messages": 0, "statuses": 0, "leads_created": 0, "auto_replies": 0}
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            integration = _resolve_integration_by_phone_number_id(
                supa, metadata.get("phone_number_id")
            )
            if not integration:
                continue

            for st in value.get("statuses") or []:
                _apply_status(supa, st)
                stats["statuses"] += 1

            name_by_wa: dict[str, Optional[str]] = {}
            for c in value.get("contacts") or []:
                if c.get("wa_id"):
                    name_by_wa[c["wa_id"]] = (c.get("profile") or {}).get("name")

            for msg in value.get("messages") or []:
                res = _handle_inbound(supa, integration, msg, name_by_wa.get(msg.get("from")))
                stats["messages"] += 1
                if res.get("lead_created"):
                    stats["leads_created"] += 1
                if res.get("auto_replied"):
                    stats["auto_replies"] += 1

    return stats
