"""Integração server-side com as APIs públicas da Azos.

Este módulo não cria propostas nem conclui vendas: as specs disponibilizadas
oferecem cotação e consultas de propostas/apólices. Nunca registre o payload
de perfil ou a API key em logs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
import re
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from supabase import Client

from app.core.config import settings
from app.services.email_service import send_system_email


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AzosClient:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        # A carteira de corretor pode devolver dezenas de propostas e oscilar
        # no gateway. GETs são idempotentes, então uma nova tentativa é segura.
        attempts = 2 if method.upper() == "GET" and path.startswith("/v1/brokers/") else 1
        response: httpx.Response | None = None
        last_error: httpx.HTTPError | None = None
        for _ in range(attempts):
            try:
                with httpx.Client(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
                    response = client.request(
                        method,
                        f"{self.base_url}{path}",
                        headers={"X-API-KEY": self.api_key},
                        **kwargs,
                    )
                break
            except httpx.HTTPError as exc:
                last_error = exc
        if response is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Não foi possível comunicar com a Azos.",
            ) from last_error

        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = {"message": response.text[:300]}
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"message": "A Azos recusou a solicitação.", "azos": detail},
            )

        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="A Azos retornou uma resposta inválida.",
            ) from exc

    def list_professions(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/platform/quotation/professions")
        return data if isinstance(data, list) else []

    def list_coverages(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        data = self._request("POST", "/v1/platform/quotation/coverages", json=profile)
        return data if isinstance(data, list) else []

    def calculate_quote(self, profile: dict[str, Any], coverages: list[dict[str, Any]]) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/v1/platform/quotation/coverages2premiums",
            json={**profile, "coverages": coverages},
        )
        return data if isinstance(data, dict) else {"coverages": []}

    def list_proposals(self, *, limit: int, offset: int) -> dict[str, Any]:
        data = self._request("GET", "/v1/platforms/proposals", params={"limit": limit, "offset": offset})
        return data if isinstance(data, dict) else {"items": []}

    def list_broker_proposals(self, *, limit: int, offset: int) -> dict[str, Any]:
        data = self._request("GET", "/v1/brokers/proposals", params={"limit": limit, "offset": offset})
        return data if isinstance(data, dict) else {"items": []}

    def list_policies(self, *, limit: int, offset: int) -> dict[str, Any]:
        data = self._request("GET", "/v1/platforms/policies", params={"limit": limit, "offset": offset})
        return data if isinstance(data, dict) else {"items": []}

    def list_broker_policies(self, *, limit: int, offset: int) -> dict[str, Any]:
        data = self._request("GET", "/v1/brokers/policies", params={"limit": limit, "offset": offset, "sort": "-updated_at"})
        return data if isinstance(data, dict) else {"items": []}

    def list_broker_commissions(self, *, limit: int, offset: int) -> dict[str, Any]:
        data = self._request("GET", "/v1/brokers/commissions", params={"limit": limit, "offset": offset})
        return data if isinstance(data, dict) else {"items": []}


def get_azos_client() -> AzosClient:
    if not settings.AZOS_API_KEY.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Integração Azos não configurada. Defina AZOS_API_KEY no backend.",
        )
    return AzosClient(api_key=settings.AZOS_API_KEY, base_url=settings.AZOS_API_BASE_URL)


def ensure_lead(supa: Client, *, org_id: str, lead_id: str) -> dict[str, Any]:
    response = supa.table("leads").select("id, org_id, nome").eq("org_id", org_id).eq("id", lead_id).maybe_single().execute()
    lead = getattr(response, "data", None)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado na organização.")
    return lead


def create_quote(
    supa: Client,
    *,
    org_id: str,
    lead_id: str,
    created_by: str | None,
    profile: dict[str, Any],
    selected_coverages: list[dict[str, Any]],
    azos: AzosClient,
    recommendation: dict[str, Any] | None = None,
    recommendation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_lead(supa, org_id=org_id, lead_id=lead_id)
    result = azos.calculate_quote(profile, selected_coverages)
    row = {
        "org_id": org_id,
        "lead_id": lead_id,
        "provider": "azos",
        "profile": profile,
        "selected_coverages": selected_coverages,
        "result": result,
        "total_premium": result.get("total_premium"),
        "consent_obtained_at": utcnow_iso(),
        "created_by": created_by,
        "recommendation": recommendation,
        "recommendation_context": recommendation_context,
    }
    saved = supa.table("seguro_azos_cotacoes").insert(row).execute()
    data = getattr(saved, "data", None) or []
    return data[0] if data else row


def register_whatsapp_handoff(
    supa: Client,
    *,
    org_id: str,
    lead_id: str,
    quote_id: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """Registra o aceite no WhatsApp para o corretor formalizar na Azos.

    Não cria contrato ou proposta na Azos e não escreve em domínios de
    Consórcio: apenas cria o atendimento pendente de Seguro.
    """
    query = (
        supa.table("seguro_azos_cotacoes")
        .select("id")
        .eq("org_id", org_id)
        .eq("lead_id", lead_id)
    )
    if quote_id:
        query = query.eq("id", quote_id)
    response = query.order("created_at", desc=True).limit(1).execute()
    quotes = getattr(response, "data", None) or []
    if not quotes:
        return {"ok": False, "erro": "Nenhuma cotação Azos foi encontrada para este cliente."}

    quote = quotes[0]
    supa.table("seguro_azos_cotacoes").update(
        {"public_status": "interesse_confirmado", "interest_confirmed_at": utcnow_iso()}
    ).eq("id", quote["id"]).eq("org_id", org_id).execute()
    supa.table("seguro_azos_atendimentos").upsert(
        {
            "org_id": org_id,
            "lead_id": lead_id,
            "cotacao_id": quote["id"],
            "origem": "whatsapp_ia",
            "updated_at": utcnow_iso(),
        },
        on_conflict="cotacao_id",
    ).execute()
    try:
        supa.table("activities").insert(
            {
                "id": str(uuid4()),
                "org_id": org_id,
                "lead_id": lead_id,
                "tipo": "whatsapp",
                "assunto": "Seguro Azos: cliente pediu continuidade com corretor",
                "conteudo": summary or "Interesse confirmado pelo cliente no WhatsApp. Formalização pendente no canal autorizado da Azos.",
            }
        ).execute()
    except Exception:
        pass
    try:
        lead_response = supa.table("leads").select("nome").eq("org_id", org_id).eq("id", lead_id).maybe_single().execute()
        org_response = supa.table("orgs").select("email_from").eq("id", org_id).maybe_single().execute()
        lead = getattr(lead_response, "data", None) or {}
        org = getattr(org_response, "data", None) or {}
        destination = org.get("email_from")
        if destination:
            lead_url = f"{settings.FRONTEND_SITE_URL.rstrip('/')}/app/leads/{lead_id}/seguro-azos"
            send_system_email(
                to=destination,
                subject=f"Seguro Azos: continuidade solicitada por {lead.get('nome') or 'cliente'}",
                text_body=(
                    "O cliente confirmou pelo WhatsApp que deseja seguir com o Seguro de Vida Azos.\n\n"
                    f"Cliente: {lead.get('nome') or '—'}\n"
                    "Ação necessária: um corretor deve continuar a formalização pelo canal autorizado da Azos.\n\n"
                    f"Abrir atendimento: {lead_url}"
                ),
            )
    except Exception:
        # O handoff e o atendimento pendente não dependem do aviso por e-mail.
        pass
    return {"ok": True, "cotacao_id": quote["id"], "atendimento": "pendente"}


def _first_name(name: str | None) -> str:
    return (name or "Cliente").strip().split(" ")[0] or "Cliente"


def _public_hash(supa: Client) -> str:
    for _ in range(5):
        candidate = secrets.token_urlsafe(24)
        existing = (
            supa.table("seguro_azos_cotacoes")
            .select("id")
            .eq("public_hash", candidate)
            .maybe_single()
            .execute()
        )
        if not getattr(existing, "data", None):
            return candidate
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Não foi possível gerar o link da proposta.")


def publish_quote(supa: Client, *, org_id: str, quote_id: str) -> dict[str, Any]:
    quote_response = (
        supa.table("seguro_azos_cotacoes")
        .select("id, lead_id, public_hash, public_status, expires_at, leads(nome)")
        .eq("id", quote_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    quote = getattr(quote_response, "data", None)
    if not quote:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cotação de Seguro não encontrada.")

    hash_value = quote.get("public_hash") or _public_hash(supa)
    expires_at = quote.get("expires_at") or (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    supa.table("seguro_azos_cotacoes").update({
        "public_hash": hash_value,
        "public_status": "enviada",
        "public_sent_at": utcnow_iso(),
        "expires_at": expires_at,
    }).eq("id", quote_id).eq("org_id", org_id).execute()

    lead = quote.get("leads") or {}
    lead_name = lead.get("nome") if isinstance(lead, dict) else None
    public_url = f"{settings.FRONTEND_SITE_URL.rstrip('/')}/seguros/{hash_value}"
    return {
        "id": quote_id,
        "public_hash": hash_value,
        "public_url": public_url,
        "whatsapp_message": (
            f"Olá, {_first_name(lead_name)}! Sua cotação de Seguro de Vida Azos está pronta. "
            f"Para ver as coberturas e nos avisar que deseja seguir com o atendimento, acesse: {public_url}"
        ),
        "expires_at": expires_at,
    }


def get_public_quote(supa: Client, *, public_hash: str) -> dict[str, Any]:
    response = (
        supa.table("seguro_azos_cotacoes")
        .select("public_status, expires_at, total_premium, result, selected_coverages, recommendation, recommendation_context, created_at, leads(nome)")
        .eq("public_hash", public_hash)
        .in_("public_status", ["enviada", "interesse_confirmado"])
        .maybe_single()
        .execute()
    )
    quote = getattr(response, "data", None)
    if not quote:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposta de Seguro não encontrada.")

    expires_at = quote.get("expires_at")
    if expires_at and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Esta proposta de Seguro expirou.")

    lead = quote.get("leads") or {}
    result = quote.get("result") or {}
    return {
        "cliente_primeiro_nome": _first_name(lead.get("nome") if isinstance(lead, dict) else None),
        "status": quote.get("public_status"),
        "expires_at": expires_at,
        "created_at": quote.get("created_at"),
        "total_premium": quote.get("total_premium") or result.get("total_premium"),
        "discount": result.get("discount"),
        "coverages": result.get("coverages") or [],
        "recommendation": quote.get("recommendation") or {},
        "recommendation_context": quote.get("recommendation_context") or {},
    }


def confirm_public_interest(
    supa: Client, *, public_hash: str, origin: str, user_agent: str | None
) -> dict[str, Any]:
    response = (
        supa.table("seguro_azos_cotacoes")
        .select("id, org_id, lead_id, public_status, expires_at")
        .eq("public_hash", public_hash)
        .in_("public_status", ["enviada", "interesse_confirmado"])
        .maybe_single()
        .execute()
    )
    quote = getattr(response, "data", None)
    if not quote:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposta de Seguro não encontrada.")

    expires_at = quote.get("expires_at")
    if expires_at and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Esta proposta de Seguro expirou.")

    first_confirmation = quote.get("public_status") != "interesse_confirmado"
    supa.table("seguro_azos_cotacoes").update({
        "public_status": "interesse_confirmado",
        "interest_confirmed_at": utcnow_iso(),
    }).eq("id", quote["id"]).execute()
    supa.table("seguro_azos_atendimentos").upsert({
        "org_id": quote["org_id"],
        "lead_id": quote["lead_id"],
        "cotacao_id": quote["id"],
        "origem": origin,
        "first_user_agent": user_agent,
        "updated_at": utcnow_iso(),
    }, on_conflict="cotacao_id").execute()
    if first_confirmation:
        try:
            lead_response = supa.table("leads").select("nome").eq("id", quote["lead_id"]).maybe_single().execute()
            org_response = supa.table("orgs").select("nome, email_from").eq("id", quote["org_id"]).maybe_single().execute()
            lead = getattr(lead_response, "data", None) or {}
            org = getattr(org_response, "data", None) or {}
            destination = org.get("email_from")
            if destination:
                lead_url = f"{settings.FRONTEND_SITE_URL.rstrip('/')}/app/leads/{quote['lead_id']}/seguro-azos"
                send_system_email(
                    to=destination,
                    subject=f"Interesse em Seguro Azos: {lead.get('nome') or 'cliente'}",
                    text_body=(
                        "O cliente confirmou interesse na proposta pública de Seguro de Vida Azos.\n\n"
                        f"Cliente: {lead.get('nome') or '—'}\n"
                        "Ação necessária: entrar em contato e seguir a formalização no canal autorizado da Azos.\n\n"
                        f"Abrir atendimento: {lead_url}"
                    ),
                )
        except Exception:
            # A confirmação do cliente e o atendimento pendente não dependem de e-mail.
            pass
    return {"ok": True, "message": "Recebemos seu interesse. Um especialista entrará em contato para seguir com a formalização pela Azos."}


def _upsert_external_records(
    supa: Client,
    *,
    org_id: str,
    resource: Literal["propostas", "apolices"],
    items: list[dict[str, Any]],
    lead_by_cpf: dict[str, str] | None = None,
) -> int:
    table = "seguro_azos_propostas" if resource == "propostas" else "seguro_azos_apolices"
    external_key = "proposal_id" if resource == "propostas" else "policy_id"
    synced = 0
    for item in items:
        external_id = item.get(external_key)
        if not external_id:
            continue
        existing = (
            supa.table(table).select("id").eq("org_id", org_id).eq("azos_id", external_id).maybe_single().execute()
        )
        payload = {
            "org_id": org_id,
            "azos_id": external_id,
            "status": item.get("status"),
            "external_updated_at": item.get("updated") or item.get("updated_at"),
            "payload": item,
            "synced_at": utcnow_iso(),
        }
        if resource == "propostas":
            insured = item.get("insured") or item.get("insured_data") or {}
            payload.update({
                "proposal_url": item.get("proposal_url"),
                "insured_name": item.get("insured_name") or insured.get("name") or insured.get("full_name"),
                "insured_cpf": _normalize_cpf(insured.get("cpf")),
            })
            insured_cpf = payload.get("insured_cpf")
            matched_lead_id = lead_by_cpf.get(insured_cpf) if insured_cpf and lead_by_cpf else None
            if matched_lead_id:
                payload["lead_id"] = matched_lead_id
        if resource == "apolices":
            insured = item.get("insured") or item.get("insured_data") or {}
            broker = item.get("broker_data") or {}
            validity = item.get("validity") or {}
            payload.update({
                "policy_number": item.get("policy_number") or item.get("external_number"),
                "policy_url": item.get("policy_url"),
                "proposal_azos_id": item.get("proposal_id"),
                "insured_name": item.get("insured_name") or insured.get("name") or insured.get("full_name"),
                "insured_cpf": _normalize_cpf(insured.get("cpf")),
                "broker_name": item.get("broker_name") or broker.get("name"),
                "broker_agent_email": item.get("broker_agent_email") or broker.get("agent_email"),
                "issued_at": item.get("issued_at"),
                "starts_at": validity.get("start_date") or item.get("start_date"),
                "ends_at": validity.get("end_date") or item.get("end_date"),
                "cancelled_at": item.get("cancellation_date"),
                "total_monthly_premium": item.get("total_monthly_premium") or item.get("premium_monthly"),
                "total_annual_premium": item.get("total_annual_premium") or item.get("premium"),
                "late_payment_days": item.get("late_payment_days"),
                "overdue_invoices_count": item.get("count_overdue_invoices"),
            })
            insured_cpf = payload.get("insured_cpf")
            matched_lead_id = lead_by_cpf.get(insured_cpf) if insured_cpf and lead_by_cpf else None
            if matched_lead_id:
                payload["lead_id"] = matched_lead_id
        if getattr(existing, "data", None):
            supa.table(table).update(payload).eq("id", existing.data["id"]).eq("org_id", org_id).execute()
        else:
            supa.table(table).insert(payload).execute()
        synced += 1
    return synced


def sync_resource(
    supa: Client,
    *,
    org_id: str,
    resource: Literal["propostas", "apolices"],
    limit: int,
    offset: int,
    azos: AzosClient,
) -> dict[str, Any]:
    try:
        response = (
            azos.list_broker_proposals(limit=limit, offset=offset)
            if resource == "propostas"
            else azos.list_broker_policies(limit=limit, offset=offset)
        )
    except HTTPException as exc:
        supa.table("seguro_azos_sync_runs").insert({
            "org_id": org_id,
            "resource": resource,
            "status": "error",
            "error_message": "Falha ao consultar a Azos.",
        }).execute()
        raise exc
    items = response.get("items") if isinstance(response.get("items"), list) else []
    lead_by_cpf = _lead_by_cpf(supa, org_id=org_id) if resource == "propostas" else None
    synced = _upsert_external_records(supa, org_id=org_id, resource=resource, items=items, lead_by_cpf=lead_by_cpf)
    pdfs_sent = _send_pending_azos_proposal_pdfs(supa, org_id=org_id) if resource == "propostas" else 0
    supa.table("seguro_azos_sync_runs").insert({
        "org_id": org_id,
        "resource": resource,
        "status": "success",
        "received_count": len(items),
        "synced_count": synced,
    }).execute()
    return {"ok": True, "resource": resource, "received": len(items), "synced": synced, "pdfs_enviados": pdfs_sent}


def _send_pending_azos_proposal_pdfs(supa: Client, *, org_id: str) -> int:
    """Envia uma única vez o PDF oficial Azos de propostas vinculadas por CPF."""
    from app.services import whatsapp_service as wa

    integration = wa.get_integration_row(supa=supa, org_id=org_id)
    if not integration or not integration.get("ativo"):
        return 0
    access_token = (integration.get("access_token") or "").strip()
    phone_number_id = (integration.get("phone_number_id") or "").strip()
    if not access_token or not phone_number_id:
        return 0

    response = (
        supa.table("seguro_azos_propostas")
        .select("id, lead_id, proposal_url, insured_name, leads(telefone)")
        .eq("org_id", org_id)
        .not_.is_("lead_id", "null")
        .not_.is_("proposal_url", "null")
        .is_("pdf_sent_at", "null")
        .limit(50)
        .execute()
    )
    sent = 0
    for proposal in getattr(response, "data", None) or []:
        url = str(proposal.get("proposal_url") or "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".azos.com.br"):
            continue
        lead = proposal.get("leads") or {}
        phone = re.sub(r"\D", "", str(lead.get("telefone") or ""))
        if not phone:
            continue
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                pdf_response = client.get(url)
            if pdf_response.status_code >= 400 or not pdf_response.content:
                continue
            media_id = wa.upload_media(
                access_token=access_token,
                phone_number_id=phone_number_id,
                data=pdf_response.content,
                mime="application/pdf",
                filename="proposta-seguro-azos.pdf",
            )
            if not media_id:
                continue
            outbound = wa.send_document_message(
                access_token=access_token,
                phone_number_id=phone_number_id,
                to=phone,
                media_id=media_id,
                filename="proposta-seguro-azos.pdf",
                caption="Sua proposta oficial de Seguro de Vida Azos.",
            )
            messages = outbound.get("messages") if isinstance(outbound, dict) else []
            wa_message_id = messages[0].get("id") if isinstance(messages, list) and messages else None
            supa.table("whatsapp_messages").insert({
                "org_id": org_id,
                "lead_id": proposal.get("lead_id"),
                "direction": "out",
                "wa_message_id": wa_message_id,
                "phone": phone,
                "msg_type": "document",
                "body": "[PDF Azos] Proposta oficial de Seguro de Vida",
                "status": "sent",
                "payload": {"seguro_azos": True, "proposal_pdf": True},
            }).execute()
            supa.table("seguro_azos_propostas").update({"pdf_sent_at": utcnow_iso()}).eq("id", proposal["id"]).eq("org_id", org_id).execute()
            sent += 1
        except Exception:
            continue
    return sent


def _upsert_broker_commissions(supa: Client, *, org_id: str, items: list[dict[str, Any]]) -> int:
    synced = 0
    for item in items:
        external_id = item.get("id")
        if not external_id:
            continue
        policy = item.get("policy") or {}
        insured = item.get("insured") or {}
        invoice = item.get("invoice") or {}
        row = {
            "org_id": org_id,
            "azos_id": external_id,
            "policy_azos_id": policy.get("id"),
            "policy_number": policy.get("external_number"),
            "insured_name": insured.get("name"),
            "broker_agent_email": item.get("broker_agent_email"),
            "invoice_azos_id": invoice.get("id"),
            "invoice_sequence_number": invoice.get("sequence_number"),
            "invoice_value": invoice.get("value"),
            "invoice_paid_at": invoice.get("paid_at"),
            "commission_value": item.get("commission_value") or 0,
            "commission_percentage": item.get("commission_percentage"),
            "paid_at": item.get("paid_at"),
            "status": item.get("status"),
            "payload": item,
            "synced_at": utcnow_iso(),
        }
        existing = (
            supa.table("seguro_azos_comissoes").select("id")
            .eq("org_id", org_id).eq("azos_id", external_id).maybe_single().execute()
        )
        if getattr(existing, "data", None):
            supa.table("seguro_azos_comissoes").update(row).eq("id", existing.data["id"]).eq("org_id", org_id).execute()
        else:
            supa.table("seguro_azos_comissoes").insert(row).execute()
        synced += 1
    return synced


def sync_broker_portfolio(supa: Client, *, org_id: str, limit: int, offset: int, azos: AzosClient) -> dict[str, Any]:
    resources = (
        ("propostas", azos.list_broker_proposals),
        ("apolices", azos.list_broker_policies),
        ("comissoes", azos.list_broker_commissions),
    )
    fetched: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    for resource_name, fetch_page in resources:
        try:
            fetched[resource_name] = _fetch_all_broker_items(fetch_page, limit=limit, offset=offset)
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                azos_detail = detail.get("azos")
                if isinstance(azos_detail, dict):
                    detail = azos_detail.get("detail") or azos_detail.get("message") or detail
                else:
                    detail = detail.get("message") or detail
            errors[resource_name] = str(detail)
            fetched[resource_name] = []
            supa.table("seguro_azos_sync_runs").insert({
                "org_id": org_id,
                "resource": resource_name,
                "status": "error",
                "error_message": f"Falha ao consultar {resource_name} da conta de corretor Azos.",
            }).execute()
    if len(errors) == len(resources):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": "A Azos recusou todos os recursos da sincronização.", "recursos": errors},
        )
    proposals = fetched["propostas"]
    policies = fetched["apolices"]
    commissions = fetched["comissoes"]
    lead_by_cpf = _lead_by_cpf(supa, org_id=org_id)
    proposals_synced = (
        _upsert_external_records(supa, org_id=org_id, resource="propostas", items=proposals, lead_by_cpf=lead_by_cpf)
        if "propostas" not in errors else 0
    )
    proposal_pdfs_sent = (
        _send_pending_azos_proposal_pdfs(supa, org_id=org_id)
        if "propostas" not in errors else 0
    )
    policies_synced = (
        _upsert_external_records(supa, org_id=org_id, resource="apolices", items=policies, lead_by_cpf=lead_by_cpf)
        if "apolices" not in errors else 0
    )
    commissions_synced = (
        _upsert_broker_commissions(supa, org_id=org_id, items=commissions)
        if "comissoes" not in errors else 0
    )
    sync_results = (
        ("propostas", len(proposals), proposals_synced),
        ("apolices", len(policies), policies_synced),
        ("comissoes", len(commissions), commissions_synced),
    )
    for resource, received, synced in sync_results:
        if resource in errors:
            continue
        supa.table("seguro_azos_sync_runs").insert({
            "org_id": org_id, "resource": resource, "status": "success", "received_count": received, "synced_count": synced,
        }).execute()
    associated = sum(1 for item in policies if _normalize_cpf((item.get("insured_data") or {}).get("cpf")) in lead_by_cpf)
    return {
        "ok": True,
        "propostas": {"received": len(proposals), "synced": proposals_synced, "pdfs_enviados": proposal_pdfs_sent},
        "apolices": {"received": len(policies), "synced": policies_synced, "associadas_por_cpf": associated},
        "comissoes": {"received": len(commissions), "synced": commissions_synced},
        "avisos": [f"{resource}: {message}" for resource, message in errors.items()],
    }


def _fetch_all_broker_items(fetch_page: Any, *, limit: int, offset: int) -> list[dict[str, Any]]:
    """Percorre as páginas da Azos para evitar uma carteira parcialmente sincronizada."""
    items: list[dict[str, Any]] = []
    current_offset = offset
    while True:
        response = fetch_page(limit=limit, offset=current_offset)
        page = response.get("items") if isinstance(response.get("items"), list) else []
        items.extend(page)
        total = response.get("total")
        if not page or len(page) < limit or (isinstance(total, int) and len(items) >= total):
            return items
        current_offset += len(page)


def _normalize_cpf(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 11 else None


def _lead_by_cpf(supa: Client, *, org_id: str) -> dict[str, str]:
    """Retorna somente CPFs que apontam para um único lead da mesma organização."""
    response = (
        supa.table("lead_cadastros")
        .select("lead_id, lead_cadastros_pf(cpf)")
        .eq("org_id", org_id)
        .execute()
    )
    candidates: dict[str, set[str]] = {}
    for cadastro in getattr(response, "data", None) or []:
        pf_rows = cadastro.get("lead_cadastros_pf") or []
        if isinstance(pf_rows, dict):
            pf_rows = [pf_rows]
        for pf in pf_rows:
            cpf = _normalize_cpf(pf.get("cpf") if isinstance(pf, dict) else None)
            lead_id = cadastro.get("lead_id")
            if cpf and lead_id:
                candidates.setdefault(cpf, set()).add(lead_id)
    return {cpf: next(iter(lead_ids)) for cpf, lead_ids in candidates.items() if len(lead_ids) == 1}


def list_broker_portfolio(supa: Client, *, org_id: str, status_filter: str | None = None) -> dict[str, Any]:
    policies_query = supa.table("seguro_azos_apolices").select(
        "id, azos_id, lead_id, policy_number, policy_url, insured_name, broker_name, status, starts_at, ends_at, total_monthly_premium, late_payment_days, overdue_invoices_count, external_updated_at"
    ).eq("org_id", org_id).order("external_updated_at", desc=True).limit(500)
    commissions_query = supa.table("seguro_azos_comissoes").select(
        "id, azos_id, policy_azos_id, policy_number, insured_name, commission_value, commission_percentage, paid_at, status, broker_agent_email, invoice_sequence_number, invoice_value, invoice_paid_at"
    ).eq("org_id", org_id).order("paid_at", desc=True).limit(500)
    if status_filter:
        policies_query = policies_query.eq("status", status_filter)
    policies_response = policies_query.execute()
    commissions_response = commissions_query.execute()
    policies = getattr(policies_response, "data", None) or []
    commissions = getattr(commissions_response, "data", None) or []
    active_policies = [item for item in policies if item.get("status") == "in_effect"]
    overdue_policies = [
        item for item in policies
        if item.get("status") == "overdue" or (item.get("overdue_invoices_count") or 0) > 0
    ]
    recurring_monthly_premium = sum(float(item.get("total_monthly_premium") or 0) for item in active_policies)
    premium_at_risk = sum(float(item.get("total_monthly_premium") or 0) for item in overdue_policies)
    paid_commission = sum(float(item.get("commission_value") or 0) for item in commissions if item.get("status") == "paid")
    scheduled_commission = sum(float(item.get("commission_value") or 0) for item in commissions if item.get("status") == "scheduled_payment")
    verification_commission = sum(float(item.get("commission_value") or 0) for item in commissions if item.get("status") == "awaiting_verification")
    commission_total = paid_commission + scheduled_commission + verification_commission
    return {
        "apolices": policies,
        "comissoes": commissions,
        "resumo": {
            "apolices_ativas": len(active_policies),
            "apolices_em_atraso": len(overdue_policies),
            "apolices_inativas": sum(1 for item in policies if item.get("status") in {"canceled", "defeated"}),
            "premio_mensal_recorrente": recurring_monthly_premium,
            "premio_anualizado": recurring_monthly_premium * 12,
            "premio_mensal_em_risco": premium_at_risk,
            "ticket_medio_mensal": recurring_monthly_premium / len(active_policies) if active_policies else 0,
            "comissao_paga": paid_commission,
            "comissao_programada": scheduled_commission,
            "comissao_em_conferencia": verification_commission,
            "comissao_a_receber": scheduled_commission + verification_commission,
            "comissao_total": commission_total,
            "realizacao_comissao_pct": (paid_commission / commission_total * 100) if commission_total else 0,
        },
    }
