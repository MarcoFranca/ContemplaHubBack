from __future__ import annotations

from typing import Optional, Literal, Dict, Any, List

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from supabase import Client

from app.routers.carteira import ensure_carteira_cliente
from app.deps import get_supabase_admin
from app.services.contract_partner_sync_service import sync_contrato_parceiros_for_contract
from app.services.kanban_service import move_lead_stage

router = APIRouter(prefix="/contracts", tags=["contracts"])


# ======================================================
# INPUTS
# ======================================================
class LanceFixoOpcaoIn(BaseModel):
    percentual: float = Field(gt=0, le=100)
    ordem: int = Field(ge=1)
    ativo: bool = True
    observacoes: Optional[str] = None


class ContractFromLeadIn(BaseModel):
    # Identificação
    lead_id: str
    administradora_id: str

    # Cota
    numero_cota: str
    grupo_codigo: str
    produto: Literal["imobiliario", "auto"] = "imobiliario"

    # Valores
    valor_carta: str
    prazo: Optional[int] = None
    forma_pagamento: Optional[str] = None
    indice_correcao: Optional[str] = None
    valor_parcela: Optional[str] = None

    # Flags
    parcela_reduzida: bool = False
    fgts_permitido: bool = False
    embutido_permitido: bool = False
    autorizacao_gestao: bool = False

    # Datas / contrato
    data_adesao: Optional[str] = None   # yyyy-mm-dd
    data_assinatura: Optional[str] = None
    numero_contrato: Optional[str] = None

    # Novas opções de lance fixo
    opcoes_lance_fixo: List[LanceFixoOpcaoIn] = []


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


def _validate_opcoes_lance_fixo(opcoes: List[LanceFixoOpcaoIn]) -> None:
    if not opcoes:
        return

    ordens = set()
    percentuais = set()

    for op in opcoes:
        if op.ordem in ordens:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ordem duplicada nas opções de lance fixo",
            )

        pct_key = f"{op.percentual:.4f}"
        if pct_key in percentuais:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Percentual duplicado nas opções de lance fixo",
            )

        ordens.add(op.ordem)
        percentuais.add(pct_key)


# ======================================================
# POST /contracts/from-lead
# Cria COTA + CONTRATO (status pendente_assinatura)
# garante entrada na carteira
# salva opções de lance fixo, se houver
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

    # 2) Normalizar valores
    valor_carta = _parse_money(body.valor_carta)
    if valor_carta is None:
        raise HTTPException(400, "Valor da carta inválido")

    valor_parcela = _parse_money(body.valor_parcela)

    # 3) Validar opções de lance fixo
    _validate_opcoes_lance_fixo(body.opcoes_lance_fixo)

    # 4) Criar COTA
    cota_payload = {
        "org_id": x_org_id,
        "lead_id": body.lead_id,
        "administradora_id": body.administradora_id,
        "numero_cota": body.numero_cota,
        "grupo_codigo": body.grupo_codigo,
        "produto": body.produto,
        "valor_carta": valor_carta,
        "valor_parcela": valor_parcela,
        "prazo": body.prazo,
        "forma_pagamento": body.forma_pagamento,
        "indice_correcao": body.indice_correcao,
        "parcela_reduzida": body.parcela_reduzida,
        "embutido_permitido": body.embutido_permitido,
        "fgts_permitido": body.fgts_permitido,
        "autorizacao_gestao": body.autorizacao_gestao,
        "data_adesao": body.data_adesao,
        "status": "ativa",
    }

    cota_resp = (
        supa.table("cotas")
        .insert(cota_payload, returning="representation")
        .execute()
    )
    if not getattr(cota_resp, "data", None):
        raise HTTPException(500, "Erro ao criar cota")

    cota = cota_resp.data[0]
    cota_id = cota["id"]

    # 5) Salvar opções de lance fixo, se houver
    if body.opcoes_lance_fixo:
        fixo_rows = [
            {
                "org_id": x_org_id,
                "cota_id": cota_id,
                "percentual": op.percentual,
                "ordem": op.ordem,
                "ativo": op.ativo,
                "observacoes": op.observacoes,
            }
            for op in body.opcoes_lance_fixo
        ]

        fixo_resp = (
            supa.table("cota_lance_fixo_opcoes")
            .insert(fixo_rows, returning="representation")
            .execute()
        )

        if not getattr(fixo_resp, "data", None):
            raise HTTPException(500, "Erro ao salvar opções de lance fixo")

    # 6) Criar CONTRATO
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

    # 7) Garantir entrada na carteira
    carteira_result = ensure_carteira_cliente(
        supa=supa,
        org_id=x_org_id,
        lead_id=body.lead_id,
        origem_entrada="contrato",
        observacoes="Entrada automática na carteira ao gerar contrato",
    )

    # NOVO: sincroniza parceiro(s) do contrato a partir da cota
    partner_sync = sync_contrato_parceiros_for_contract(
        supa,
        org_id=x_org_id,
        contract_id=contrato_id,
        actor_id=None,
    )

    return {
        "ok": True,
        "cota_id": cota_id,
        "contrato_id": contrato_id,
        "status": contrato_resp.data[0]["status"],
        "carteira": carteira_result["carteira_cliente"],
        "carteira_created": carteira_result["created"],
        "opcoes_lance_fixo_count": len(body.opcoes_lance_fixo),
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
    )
    cota = getattr(cota_resp, "data", None)

    if not cota:
        raise HTTPException(500, "Cota associada não encontrada")

    lead_id = cota["lead_id"]

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