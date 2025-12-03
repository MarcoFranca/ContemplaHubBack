from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.deps import get_supabase_admin

router = APIRouter(prefix="/lead-cadastros", tags=["lead-cadastros"])


@router.get("/p/{token}")
def api_get_lead_cadastro_public(
    token: str,
    supa: Client = Depends(get_supabase_admin),
) -> Dict[str, Any]:
    """
    Endpoint público para carregar um lead_cadastros pelo token_publico.

    Usado pela página /cadastro/[token] no front.
    """

    try:
        resp = (
            supa.table("lead_cadastros")
            .select("*")
            .eq("token_publico", token)
            .execute()
        )
    except Exception as e:
        print("ERRO ao buscar lead_cadastros por token:", repr(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao buscar cadastro.",
        )

    data = getattr(resp, "data", None)

    row: Dict[str, Any] | None = None
    if isinstance(data, list) and data:
        row = data[0]
    elif isinstance(data, dict) and data:
        row = data

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cadastro não encontrado.",
        )

    # Filtra só o que o front realmente precisa
    return {
        "id": row.get("id"),
        "org_id": row.get("org_id"),
        "lead_id": row.get("lead_id"),
        "proposta_id": row.get("proposta_id"),
        "tipo_cliente": row.get("tipo_cliente"),
        "status": row.get("status"),
        "token_publico": row.get("token_publico"),
    }
