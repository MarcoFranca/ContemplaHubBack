"""Integração server-side com as APIs públicas da Azos.

Este módulo não cria propostas nem conclui vendas: as specs disponibilizadas
oferecem cotação e consultas de propostas/apólices. Nunca registre o payload
de perfil ou a API key em logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import HTTPException, status
from supabase import Client

from app.core.config import settings


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
