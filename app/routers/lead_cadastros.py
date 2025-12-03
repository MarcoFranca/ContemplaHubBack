# app/routers/lead_cadastros.py
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.deps import get_supabase_admin

router = APIRouter(prefix="/lead-cadastros", tags=["lead-cadastros"])


class LeadCadastroPFInput(BaseModel):
    nome_completo: str
    cpf: str
    data_nascimento: Optional[str] = None
    estado_civil: Optional[str] = None
    email: str
    telefone_celular: str
    renda_mensal: Optional[float] = None
    cep: Optional[str] = None
    endereco: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    uf: Optional[str] = None
    observacoes: Optional[str] = None


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


@router.patch("/p/{token}/pf")
def api_update_lead_cadastro_pf(
    token: str,
    body: LeadCadastroPFInput,
    supa: Client = Depends(get_supabase_admin),
):
    """
    Atualiza os dados PF vinculados a um lead_cadastros identificado pelo token_publico.

    Fluxo:
    - busca lead_cadastros pelo token;
    - se não achar => 404;
    - faz upsert em lead_cadastros_pf (um registro por cadastro_id);
    - opcional: atualiza status do lead_cadastros para 'dados_recebidos'.
    """

    # 1) achar o cadastro base pelo token
    try:
        resp = (
            supa.table("lead_cadastros")
            .select("id, org_id, lead_id, proposta_id, tipo_cliente, status")
            .eq("token_publico", token)
            .limit(1)
            .execute()
        )
    except Exception as e:
        print("ERRO ao buscar lead_cadastros por token (PF):", repr(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao buscar cadastro (PF).",
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
            detail="Cadastro não encontrado para este token.",
        )

    cadastro_id = row["id"]

    # 2) upsert na tabela de dados PF
    upsert_data = {
        "cadastro_id": cadastro_id,
        "nome_completo": body.nome_completo,
        "cpf": body.cpf,
        "data_nascimento": body.data_nascimento,
        "estado_civil": body.estado_civil,
        "email": body.email,
        "telefone_celular": body.telefone_celular,
        "renda_mensal": body.renda_mensal,
        "cep": body.cep,
        "endereco": body.endereco,
        "bairro": body.bairro,
        "cidade": body.cidade,
        "uf": body.uf,
        "observacoes": body.observacoes,
    }

    try:
        # ⚠️ se o nome da tabela for outro, ajusta aqui
        resp_upsert = (
            supa.table("lead_cadastros_pf")
            .upsert(upsert_data, on_conflict="cadastro_id")
            .execute()
        )
        print("DEBUG upsert lead_cadastros_pf:", getattr(resp_upsert, "data", None))
    except Exception as e:
        print("ERRO ao upsert em lead_cadastros_pf:", repr(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao salvar dados PF.",
        )

    # 3) opcional: atualizar status do cadastro principal
    try:
        supa.table("lead_cadastros").update(
            {"status": "dados_recebidos"}
        ).eq("id", cadastro_id).execute()
    except Exception as e:
        print("WARN: falha ao atualizar status de lead_cadastros:", repr(e))

    return {"ok": True}
