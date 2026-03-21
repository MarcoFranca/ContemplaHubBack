# app/services/contract_partner_sync_service.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException
from supabase import Client


SYNC_ORIGEM = "cota_comissao_parceiro"


def utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def _safe_data(resp: Any) -> Any:
    return getattr(resp, "data", None)


def insert_audit_log(
    supa: Client,
    *,
    org_id: str,
    actor_id: Optional[str],
    entity: str,
    entity_id: Optional[str],
    action: str,
    diff: Optional[dict] = None,
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


def get_contract_or_404(
    supa: Client,
    *,
    org_id: str,
    contract_id: str,
) -> Dict[str, Any]:
    resp = (
        supa.table("contratos")
        .select("id, org_id, cota_id, numero, status")
        .eq("org_id", org_id)
        .eq("id", contract_id)
        .maybe_single()
        .execute()
    )
    data = _safe_data(resp)
    if not data:
        raise HTTPException(404, "Contrato não encontrado")
    return data


def get_cota_or_404(
    supa: Client,
    *,
    org_id: str,
    cota_id: str,
) -> Dict[str, Any]:
    resp = (
        supa.table("cotas")
        .select("id, org_id, numero_cota, grupo_codigo, status")
        .eq("org_id", org_id)
        .eq("id", cota_id)
        .maybe_single()
        .execute()
    )
    data = _safe_data(resp)
    if not data:
        raise HTTPException(404, "Cota não encontrada")
    return data


def fetch_active_partner_ids_for_cota(
    supa: Client,
    *,
    org_id: str,
    cota_id: str,
) -> List[str]:
    resp = (
        supa.table("cota_comissao_parceiros")
        .select("parceiro_id")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .eq("ativo", True)
        .execute()
    )
    rows = _safe_data(resp) or []
    return [row["parceiro_id"] for row in rows if row.get("parceiro_id")]


def fetch_synced_partner_links_for_contract(
    supa: Client,
    *,
    org_id: str,
    contract_id: str,
) -> List[Dict[str, Any]]:
    resp = (
        supa.table("contrato_parceiros")
        .select("id, parceiro_id, origem")
        .eq("org_id", org_id)
        .eq("contrato_id", contract_id)
        .eq("origem", SYNC_ORIGEM)
        .execute()
    )
    return _safe_data(resp) or []


def fetch_contracts_by_cota(
    supa: Client,
    *,
    org_id: str,
    cota_id: str,
) -> List[Dict[str, Any]]:
    resp = (
        supa.table("contratos")
        .select("id, org_id, cota_id, numero, status")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .order("created_at", desc=False)
        .execute()
    )
    return _safe_data(resp) or []


def remove_synced_partner_links_for_contract(
    supa: Client,
    *,
    org_id: str,
    contract_id: str,
    actor_id: Optional[str] = None,
    reason: str = "manual_cleanup",
) -> dict:
    existing = fetch_synced_partner_links_for_contract(
        supa,
        org_id=org_id,
        contract_id=contract_id,
    )

    removed_ids: List[str] = []

    for row in existing:
        (
            supa.table("contrato_parceiros")
            .delete()
            .eq("org_id", org_id)
            .eq("id", row["id"])
            .execute()
        )
        removed_ids.append(row["id"])

    if removed_ids:
        insert_audit_log(
            supa,
            org_id=org_id,
            actor_id=actor_id,
            entity="contrato",
            entity_id=contract_id,
            action="contract_partner_links_removed",
            diff={
                "origem": SYNC_ORIGEM,
                "removed_count": len(removed_ids),
                "reason": reason,
            },
        )

    return {
        "ok": True,
        "contract_id": contract_id,
        "removed_count": len(removed_ids),
        "removed_ids": removed_ids,
    }


def sync_contrato_parceiros_for_contract(
    supa: Client,
    *,
    org_id: str,
    contract_id: str,
    actor_id: Optional[str] = None,
) -> dict:
    contrato = get_contract_or_404(
        supa,
        org_id=org_id,
        contract_id=contract_id,
    )

    cota_id = contrato["cota_id"]
    if not cota_id:
        raise HTTPException(400, "Contrato sem cota vinculada")

    desired_partner_ids: Set[str] = set(
        fetch_active_partner_ids_for_cota(
            supa,
            org_id=org_id,
            cota_id=cota_id,
        )
    )

    current_rows = fetch_synced_partner_links_for_contract(
        supa,
        org_id=org_id,
        contract_id=contract_id,
    )
    current_partner_ids: Set[str] = {
        row["parceiro_id"]
        for row in current_rows
        if row.get("parceiro_id")
    }

    to_insert = sorted(desired_partner_ids - current_partner_ids)
    to_delete_partner_ids = sorted(current_partner_ids - desired_partner_ids)

    inserted = 0
    removed = 0

    now_iso = utcnow_iso()

    if to_insert:
        rows = [
            {
                "org_id": org_id,
                "contrato_id": contract_id,
                "parceiro_id": parceiro_id,
                "origem": SYNC_ORIGEM,
                "principal": False,
                "observacoes": "Vínculo sincronizado automaticamente a partir de cota_comissao_parceiros",
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            for parceiro_id in to_insert
        ]

        resp = supa.table("contrato_parceiros").insert(rows, returning="representation").execute()
        inserted_rows = _safe_data(resp) or []
        inserted = len(inserted_rows)

    if to_delete_partner_ids:
        current_by_partner = {
            row["parceiro_id"]: row
            for row in current_rows
            if row.get("parceiro_id")
        }
        for parceiro_id in to_delete_partner_ids:
            row = current_by_partner.get(parceiro_id)
            if not row:
                continue
            (
                supa.table("contrato_parceiros")
                .delete()
                .eq("org_id", org_id)
                .eq("id", row["id"])
                .eq("origem", SYNC_ORIGEM)
                .execute()
            )
            removed += 1

    if inserted or removed:
        insert_audit_log(
            supa,
            org_id=org_id,
            actor_id=actor_id,
            entity="contrato",
            entity_id=contract_id,
            action="contract_partner_links_synced",
            diff={
                "origem": SYNC_ORIGEM,
                "inserted": inserted,
                "removed": removed,
                "desired_partner_ids": sorted(list(desired_partner_ids)),
                "current_partner_ids_before": sorted(list(current_partner_ids)),
            },
        )

    return {
        "ok": True,
        "contract_id": contract_id,
        "cota_id": cota_id,
        "inserted": inserted,
        "removed": removed,
        "desired_partner_ids": sorted(list(desired_partner_ids)),
    }


def sync_contrato_parceiros_for_cota(
    supa: Client,
    *,
    org_id: str,
    cota_id: str,
    actor_id: Optional[str] = None,
) -> dict:
    _ = get_cota_or_404(supa, org_id=org_id, cota_id=cota_id)

    contratos = fetch_contracts_by_cota(
        supa,
        org_id=org_id,
        cota_id=cota_id,
    )

    results: List[dict] = []
    total_inserted = 0
    total_removed = 0

    for contrato in contratos:
        result = sync_contrato_parceiros_for_contract(
            supa,
            org_id=org_id,
            contract_id=contrato["id"],
            actor_id=actor_id,
        )
        total_inserted += result["inserted"]
        total_removed += result["removed"]
        results.append(result)

    insert_audit_log(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        entity="cota",
        entity_id=cota_id,
        action="cota_contract_partner_links_synced",
        diff={
            "contracts_count": len(contratos),
            "total_inserted": total_inserted,
            "total_removed": total_removed,
            "origem": SYNC_ORIGEM,
        },
    )

    return {
        "ok": True,
        "cota_id": cota_id,
        "contracts_count": len(contratos),
        "total_inserted": total_inserted,
        "total_removed": total_removed,
        "results": results,
    }


def remove_synced_partner_links_for_cota(
    supa: Client,
    *,
    org_id: str,
    cota_id: str,
    actor_id: Optional[str] = None,
    reason: str = "cota_comissao_removed",
) -> dict:
    contratos = fetch_contracts_by_cota(
        supa,
        org_id=org_id,
        cota_id=cota_id,
    )

    total_removed = 0
    results: List[dict] = []

    for contrato in contratos:
        result = remove_synced_partner_links_for_contract(
            supa,
            org_id=org_id,
            contract_id=contrato["id"],
            actor_id=actor_id,
            reason=reason,
        )
        total_removed += result["removed_count"]
        results.append(result)

    insert_audit_log(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        entity="cota",
        entity_id=cota_id,
        action="cota_contract_partner_links_removed",
        diff={
            "contracts_count": len(contratos),
            "total_removed": total_removed,
            "origem": SYNC_ORIGEM,
            "reason": reason,
        },
    )

    return {
        "ok": True,
        "cota_id": cota_id,
        "contracts_count": len(contratos),
        "total_removed": total_removed,
        "results": results,
    }