from __future__ import annotations

from typing import Optional, Literal, Dict, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.deps import get_supabase_admin

router = APIRouter(prefix="/contracts", tags=["contracts"])


class ContractFromLeadIn(BaseModel):
    # Identificação
    lead_id: str
    administradora_id: str

    # Cota
    numero_cota: str
    grupo_codigo: str
    produto: Literal["imobiliario", "auto", "pesados"] = "imobiliario"

    # Valores (vem como string pra gente normalizar)
    valor_carta: str
    prazo: Optional[int] = None
    forma_pagamento: Optional[str] = None
    indice_correcao: Optional[str] = None

    # Flags
    parcela_reduzida: bool = False
    fgts_permitido: bool = False
    embutido_permitido: bool = False
    autorizacao_gestao: bool = False

    # Datas / contrato
    data_adesao: Optional[str] = None   # yyyy-mm-dd
    data_assinatura: Optional[str] = None
    numero_contrato: Optional[str] = None


def _parse_money(raw: Optional[str]) -> Optional[float]:
    """
    Converte '250.000,00' -> 250000.0
    """
    if not raw:
        return None
    v = raw.replace(".", "").replace(",", ".")
    try:
        n = float(v)
    except ValueError:
        return None
    return n


@router.post("/from-lead")
def create_contract_from_lead(
    body: ContractFromLeadIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> Dict[str, Any]:
    """
    Cria COTA + CONTRATO a partir de um lead,
    sem mover o lead de etapa ainda.
    """
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório",
        )

    # 1) validar lead pertence à org
    lead_resp = (
        supa.table("leads")
        .select("id, org_id")
        .eq("id", body.lead_id)
        .single()
        .execute()
    )
    lead = getattr(lead_resp, "data", None)
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead não encontrado",
        )

    if lead["org_id"] != x_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Lead pertence a outra organização",
        )

    # 2) normalizar valor_carta
    valor_carta = _parse_money(body.valor_carta)
    if valor_carta is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valor da carta inválido",
        )

    # 3) criar COTA
    cota_payload = {
        "org_id": x_org_id,
        "lead_id": body.lead_id,
        "administradora_id": body.administradora_id,
        "numero_cota": body.numero_cota,
        "grupo_codigo": body.grupo_codigo,
        "produto": body.produto,
        "valor_carta": valor_carta,
        "prazo": body.prazo,
        "forma_pagamento": body.forma_pagamento,
        "indice_correcao": body.indice_correcao,
        "parcela_reduzida": body.parcela_reduzida,
        "embutido_permitido": body.embutido_permitido,
        "fgts_permitido": body.fgts_permitido,
        "autorizacao_gestao": body.autorizacao_gestao,
        "data_adesao": body.data_adesao,
    }

    cota_resp = (
        supa.table("cotas")
        .insert(cota_payload)
        .select("id")
        .single()
        .execute()
    )
    cota = getattr(cota_resp, "data", None)
    if not cota:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao criar cota",
        )

    # 4) criar CONTRATO com status inicial correto
    contrato_payload = {
        "org_id": x_org_id,
        "deal_id": None,
        "cota_id": cota["id"],
        "numero": body.numero_contrato,
        "data_assinatura": body.data_assinatura,
        "status": "pendente_assinatura",
    }

    contrato_resp = (
        supa.table("contratos")
        .insert(contrato_payload)
        .select("id, status")
        .single()
        .execute()
    )
    contrato = getattr(contrato_resp, "data", None)
    if not contrato:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao criar contrato",
        )

    return {
        "ok": True,
        "cota_id": cota["id"],
        "contrato_id": contrato["id"],
        "contrato_status": contrato["status"],
    }
