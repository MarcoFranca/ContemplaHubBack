from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.deps import get_supabase_admin

router = APIRouter(prefix="/lead-cadastros", tags=["lead-cadastros"])


# --------------------------------------------------
# MODELO: entrada PF vinda do formulário público
# --------------------------------------------------
class LeadCadastroPFInput(BaseModel):
    # DADOS PESSOAIS
    nome_completo: str
    cpf: str
    data_nascimento: Optional[str] = None   # yyyy-mm-dd
    estado_civil: Optional[str] = None

    # CÔNJUGE
    cpf_conjuge: Optional[str] = None
    nome_conjuge: Optional[str] = None

    # CONTATO
    email: str
    telefone_fixo: Optional[str] = None
    telefone_celular: str

    # DOCUMENTO IDENTIDADE
    rg_numero: Optional[str] = None
    rg_orgao_emissor: Optional[str] = None
    rg_data_emissao: Optional[str] = None  # yyyy-mm-dd

    # NASCIMENTO / FILIAÇÃO
    cidade_nascimento: Optional[str] = None
    nome_mae: Optional[str] = None

    # PROFISSÃO / RENDA
    profissao: Optional[str] = None
    renda_mensal: Optional[float] = None

    # ENDEREÇO
    cep: Optional[str] = None
    endereco: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    uf: Optional[str] = None

    # FORMA DE PAGAMENTO (parcelas)
    # ajuste os valores conforme seu enum cadastro_forma_pagamento
    forma_pagamento: Optional[str] = None  # 'boleto' | 'cartao_credito' | 'debito_automatico'

    # CONTA PARA DEVOLUÇÃO
    banco_devolucao: Optional[str] = None
    agencia_devolucao: Optional[str] = None
    conta_devolucao: Optional[str] = None

    # CAMPO LIVRE
    observacoes: Optional[str] = None


# --------------------------------------------------
# HELPER ÚNICO: buscar cadastro por token_publico
# (igual estava)
# --------------------------------------------------
def _load_cadastro_by_token(
    supa: Client,
    token: str,
) -> Dict[str, Any] | None:
    print("[_load_cadastro_by_token] token_publico recebido:", repr(token))

    try:
        resp = (
            supa.table("lead_cadastros")
            .select("*")
            .eq("token_publico", token)
            .execute()
        )
    except Exception as e:
        print("ERRO ao buscar lead_cadastros por token:", repr(e))
        raise

    data = getattr(resp, "data", None)
    print("[_load_cadastro_by_token] resp.data:", data)

    row: Dict[str, Any] | None = None
    if isinstance(data, list) and data:
        row = data[0]
    elif isinstance(data, dict) and data:
        row = data

    return row


@router.get("/p/{token}")
def api_get_lead_cadastro_public(
    token: str,
    supa: Client = Depends(get_supabase_admin),
) -> Dict[str, Any]:
    print("GET /lead-cadastros/p/{token} -> token:", repr(token))

    try:
        row = _load_cadastro_by_token(supa, token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao buscar cadastro.",
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cadastro não encontrado.",
        )

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
def api_patch_lead_cadastro_pf(
    token: str,
    body: LeadCadastroPFInput,
    supa: Client = Depends(get_supabase_admin),
) -> Dict[str, Any]:
    print("PATCH /lead-cadastros/p/{token}/pf -> token:", repr(token))
    print("PATCH body:", body.dict())

    try:
        row = _load_cadastro_by_token(supa, token)
    except Exception as e:
        print("ERRO ao buscar lead_cadastros (PF) por token:", repr(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao buscar cadastro.",
        )

    if not row:
        print("PATCH: nenhum cadastro encontrado para token:", repr(token))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cadastro não encontrado para este token.",
        )

    print("PATCH: cadastro encontrado:", row)

    if row.get("tipo_cliente") != "pf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este cadastro não é de Pessoa Física.",
        )

    cadastro_id = row["id"]

    # 1) Montar payload para a tabela lead_cadastros_pf
    pf_payload: Dict[str, Any] = {
        "cadastro_id": cadastro_id,
        "nome_completo": body.nome_completo,
        "cpf": body.cpf,
        "data_nascimento": body.data_nascimento,
        "estado_civil": body.estado_civil,
        "nome_conjuge": body.nome_conjuge,
        "cpf_conjuge": body.cpf_conjuge,
        "nome_mae": body.nome_mae,
        "cidade_nascimento": body.cidade_nascimento,
        "nacionalidade": None,  # se quiser, adicionamos depois no form

        "email": body.email,
        "telefone_fixo": body.telefone_fixo,
        "celular": body.telefone_celular,

        "cep": body.cep,
        "endereco": body.endereco,
        "bairro": body.bairro,
        "cidade": body.cidade,
        "uf": body.uf,

        "rg_numero": body.rg_numero,
        "rg_orgao_emissor": body.rg_orgao_emissor,
        "rg_data_emissao": body.rg_data_emissao,

        "profissao": body.profissao,
        "renda_mensal": body.renda_mensal,

        "forma_pagamento": body.forma_pagamento,
        "banco_pagamento": None,
        "agencia_pagamento": None,
        "conta_pagamento": None,

        "banco_devolucao": body.banco_devolucao,
        "agencia_devolucao": body.agencia_devolucao,
        "conta_devolucao": body.conta_devolucao,

        "extra_json": {
            "observacoes": body.observacoes,
        },
    }

    print("PATCH upsert lead_cadastros_pf payload:", pf_payload)

    try:
        resp_pf = (
            supa.table("lead_cadastros_pf")
            .upsert(pf_payload, on_conflict="cadastro_id")
            .execute()
        )
    except Exception as e:
        print("ERRO ao upsert lead_cadastros_pf:", repr(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao salvar dados pessoais (PF): {e}",
        )

    print("PATCH lead_cadastros_pf upsert resp.data:", getattr(resp_pf, "data", None))

    # 2) Atualizar status principal
    update_payload: Dict[str, Any] = {
        "status": "pendente_documentos",
    }

    print("PATCH update lead_cadastros payload para id", cadastro_id, ":", update_payload)

    try:
        resp_upd = (
            supa.table("lead_cadastros")
            .update(update_payload)
            .eq("id", cadastro_id)
            .execute()
        )
    except Exception as e:
        print("ERRO ao atualizar lead_cadastros (PF):", repr(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao atualizar status do cadastro: {e}",
        )

    data_upd = getattr(resp_upd, "data", None)
    print("PATCH lead_cadastros update resp.data:", data_upd)

    if isinstance(data_upd, list) and data_upd:
        updated = data_upd[0]
    elif isinstance(data_upd, dict) and data_upd:
        updated = data_upd
    else:
        updated = {**row, **update_payload}

    return {
        "ok": True,
        "id": updated.get("id"),
        "status": updated.get("status"),
    }
