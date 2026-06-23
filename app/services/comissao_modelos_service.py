from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from fastapi import HTTPException
from supabase import Client

from app.schemas.comissoes import ComissaoModeloUpsertIn
from app.services.comissao_service import get_org_record_or_404


def _serialize_regras(payload: ComissaoModeloUpsertIn) -> List[Dict[str, Any]]:
    return [
        {
            "ordem": r.ordem,
            "tipo_evento": r.tipo_evento,
            "offset_meses": r.offset_meses,
            "percentual_comissao": float(r.percentual_comissao),
            "descricao": r.descricao,
        }
        for r in payload.regras
    ]


def _payload_dict(payload: ComissaoModeloUpsertIn) -> Dict[str, Any]:
    return {
        "nome": payload.nome.strip(),
        "descricao": (payload.descricao or "").strip() or None,
        "percentual_total": float(payload.percentual_total),
        "ativo": payload.ativo,
        "regras": _serialize_regras(payload),
    }


def list_modelos(supa: Client, org_id: str) -> List[Dict[str, Any]]:
    resp = (
        supa.table("comissao_modelos")
        .select("*")
        .eq("org_id", org_id)
        .order("ativo", desc=True)
        .order("nome")
        .execute()
    )
    return getattr(resp, "data", None) or []


def create_modelo(supa: Client, org_id: str, payload: ComissaoModeloUpsertIn) -> Dict[str, Any]:
    data = {"org_id": org_id, **_payload_dict(payload)}
    resp = supa.table("comissao_modelos").insert(data, returning="representation").execute()
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(500, "Erro ao criar modelo de comissão")
    return rows[0]


def update_modelo(
    supa: Client, org_id: str, modelo_id: str, payload: ComissaoModeloUpsertIn
) -> Dict[str, Any]:
    get_org_record_or_404(supa, "comissao_modelos", org_id, modelo_id)
    data = {**_payload_dict(payload), "updated_at": datetime.utcnow().isoformat()}
    resp = (
        supa.table("comissao_modelos")
        .update(data)
        .eq("org_id", org_id)
        .eq("id", modelo_id)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else {"id": modelo_id, **data}


def delete_modelo(supa: Client, org_id: str, modelo_id: str) -> Dict[str, Any]:
    get_org_record_or_404(supa, "comissao_modelos", org_id, modelo_id)
    supa.table("comissao_modelos").delete().eq("org_id", org_id).eq("id", modelo_id).execute()
    return {"ok": True, "id": modelo_id}
