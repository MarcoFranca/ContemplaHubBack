from __future__ import annotations

from typing import Optional, Literal, Dict, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.deps import get_supabase_admin
from app.services.kanban_service import move_lead_stage

router = APIRouter(prefix="/contracts", tags=["contracts"])


# ======================================================
# INPUT DE CRIAÇÃO DO CONTRATO + COTA
# ======================================================
class ContractFromLeadIn(BaseModel):
    # Identificação
    lead_id: str
    administradora_id: str

    # Cota
    numero_cota: str
    grupo_codigo: str
    produto: Literal["imobiliario", "auto", "pesados"] = "imobiliario"

    # Valores
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


# ======================================================
# HELPERS
# ======================================================
def _parse_money(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    v = raw.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


# ======================================================
# POST /contracts/from-lead
# Cria COTA + CONTRATO (status pendente_assinatura)
# ======================================================
@router.post("/from-lead")
def create_contract_from_lead(
    body: ContractFromLeadIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> Dict[str, Any]:

    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório",
        )

    # 1) Validar lead pertence à org
    lead_resp = (
        supa.table("leads")
        .select("id, org_id")
        .eq("id", body.lead_id)
        .single()
        .execute()
    )
    lead = getattr(lead_resp, "data", None)
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    if lead["org_id"] != x_org_id:
        raise HTTPException(403, "Lead pertence a outra organização")

    # 2) Normalizar valor carta
    valor_carta = _parse_money(body.valor_carta)
    if valor_carta is None:
        raise HTTPException(400, "Valor da carta inválido")

    # 3) Criar COTA
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
        .insert(cota_payload, returning="representation")
        .execute()
    )
    if not getattr(cota_resp, "data", None):
        raise HTTPException(500, "Erro ao criar cota")

    cota_id = cota_resp.data[0]["id"]

    # 4) Criar CONTRATO
    contrato_payload = {
        "org_id": x_org_id,
        "deal_id": None,
        "cota_id": cota_id,
        "numero": body.numero_contrato,
        "data_assinatura": body.data_assinatura,
        "status": "pendente_assinatura",
    }

    contrato_resp = (
        supa.table("contratos")
        .insert(contrato_payload, returning="representation")
        .execute()
    )
    if not getattr(contrato_resp, "data", None):
        raise HTTPException(500, "Erro ao criar contrato")

    contrato_id = contrato_resp.data[0]["id"]

    return {
        "ok": True,
        "cota_id": cota_id,
        "contrato_id": contrato_id,
        "status": contrato_resp.data[0]["status"],
    }


# ======================================================
# MODELO PARA UPDATE DE STATUS
# ======================================================
class ContractStatusUpdateIn(BaseModel):
    status: Literal[
        "pendente_assinatura",
        "pendente_pagamento",
        "alocado",
        "contemplado",
        "cancelado",
    ]
    observacao: Optional[str] = None


# ======================================================
# PATCH /contracts/{id}/status
# Atualiza status + aplica regra automática no lead
# ======================================================
@router.patch("/{contract_id}/status")
def update_contract_status(
    contract_id: str,
    body: ContractStatusUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(400, "X-Org-Id obrigatório")

    novo_status = body.status

    # Transições válidas
    transicoes_validas = {
        "pendente_assinatura": ["pendente_pagamento", "cancelado"],
        "pendente_pagamento": ["alocado", "cancelado"],
        "alocado": ["contemplado", "cancelado"],
        "contemplado": ["cancelado"],
    }

    correcoes_validas = {
        # Permite voltar um passo
        "pendente_pagamento": ["pendente_assinatura"],
        "alocado": ["pendente_pagamento", "pendente_assinatura"],
        "contemplado": ["alocado", "pendente_pagamento"],
        # Se cancelou errado, deixa restaurar
        "cancelado": ["pendente_pagamento", "pendente_assinatura", "alocado"],
    }

    # 1) Carregar contrato
    resp = (
        supa.table("contratos")
        .select("id, org_id, status, cota_id")
        .eq("id", contract_id)
        .single()
        .execute()
    )
    contrato = getattr(resp, "data", None)

    if not contrato:
        raise HTTPException(404, "Contrato não encontrado")

    if contrato["org_id"] != x_org_id:
        raise HTTPException(403, "Contrato pertence a outra organização")

    atual_status = contrato["status"]
    novo_status = body.status

    # Se não mudou nada, não precisa nem bater no banco
    if novo_status == atual_status:
        return {
            "ok": True,
            "contrato_id": contract_id,
            "status_anterior": atual_status,
            "status_novo": novo_status,
            "lead_afetado": None,
            "lead_movido_para": None,
        }

    allowed_next = transicoes_validas.get(atual_status, [])
    allowed_fix = correcoes_validas.get(atual_status, [])

    if novo_status not in allowed_next and novo_status not in allowed_fix:
        raise HTTPException(
            400,
            f"Transição inválida: {atual_status} → {novo_status}",
        )

    # 2) Atualizar contrato
    _ = (
        supa.table("contratos")
        .update({"status": novo_status})
        .eq("id", contract_id)
        .execute()
    )

    # 3) Buscar lead da cota
    cota_resp = (
        supa.table("cotas")
        .select("lead_id, org_id")
        .eq("id", contrato["cota_id"])
        .single()
        .execute()
    ).data

    if not cota_resp:
        raise HTTPException(500, "Cota associada não encontrada")

    lead_id = cota_resp["lead_id"]

    # 4) Regras automáticas de funil
    lead_stage_target = None

    if novo_status == "alocado":
        lead_stage_target = "ativo"

    elif novo_status == "cancelado":
        lead_stage_target = "perdido"

    if lead_stage_target:
        result = move_lead_stage(
            org_id=x_org_id,
            lead_id=lead_id,
            new_stage=lead_stage_target,  # type: ignore
            supa=supa,
            reason=f"Contrato {novo_status}",
        )

        if not result.get("ok"):
            print("WARN: Falha ao mover lead automaticamente:", result)

    return {
        "ok": True,
        "contrato_id": contract_id,
        "status_anterior": atual_status,
        "status_novo": novo_status,
        "lead_afetado": lead_id,
        "lead_movido_para": lead_stage_target,
    }
