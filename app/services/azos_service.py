"""Integração server-side com as APIs públicas da Azos.

Este módulo não cria propostas nem conclui vendas: as specs disponibilizadas
oferecem cotação e consultas de propostas/apólices. Nunca registre o payload
de perfil ou a API key em logs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from typing import Any, Literal

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
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={"X-API-KEY": self.api_key},
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Não foi possível comunicar com a Azos.",
            ) from exc

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

    def list_policies(self, *, limit: int, offset: int) -> dict[str, Any]:
        data = self._request("GET", "/v1/platforms/policies", params={"limit": limit, "offset": offset})
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
    created_by: str,
    profile: dict[str, Any],
    selected_coverages: list[dict[str, Any]],
    azos: AzosClient,
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
    }
    saved = supa.table("seguro_azos_cotacoes").insert(row).execute()
    data = getattr(saved, "data", None) or []
    return data[0] if data else row


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
        .select("public_status, expires_at, total_premium, result, selected_coverages, created_at, leads(nome)")
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
            azos.list_proposals(limit=limit, offset=offset)
            if resource == "propostas"
            else azos.list_policies(limit=limit, offset=offset)
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
    synced = _upsert_external_records(supa, org_id=org_id, resource=resource, items=items)
    supa.table("seguro_azos_sync_runs").insert({
        "org_id": org_id,
        "resource": resource,
        "status": "success",
        "received_count": len(items),
        "synced_count": synced,
    }).execute()
    return {"ok": True, "resource": resource, "received": len(items), "synced": synced}
