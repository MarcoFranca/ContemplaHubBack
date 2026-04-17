from __future__ import annotations

from typing import Optional, Literal, Dict, Any, List

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from supabase import Client

from app.routers.carteira import ensure_carteira_cliente
from app.deps import get_supabase_admin
from app.services.contract_partner_sync_service import sync_contrato_parceiros_for_contract
from app.services.kanban_service import move_lead_stage
from decimal import Decimal

from app.schemas.comissoes import (
    CotaComissaoConfigUpsertIn,
    CotaComissaoParceiroIn,
    ComissaoRegraIn,
)
from app.services.comissao_service import (
    upsert_config_for_cota,
    generate_lancamentos_for_contrato,
)

router = APIRouter(prefix="/contracts", tags=["contracts"])

CONTRACT_STATUS_VALUES = (
    "pendente_assinatura",
    "pendente_pagamento",
    "alocado",
    "contemplado",
    "cancelado",
)

COTA_SITUACAO_VALUES = (
    "ativa",
    "contemplada",
    "cancelada",
)

TRANSICOES_VALIDAS = {
    "pendente_assinatura": ["pendente_pagamento", "cancelado"],
    "pendente_pagamento": ["alocado", "cancelado"],
    "alocado": ["contemplado", "cancelado"],
    "contemplado": ["cancelado"],
}

CORRECOES_VALIDAS = {
    "pendente_pagamento": ["pendente_assinatura"],
    "alocado": ["pendente_pagamento", "pendente_assinatura"],
    "contemplado": ["alocado", "pendente_pagamento"],
    "cancelado": ["pendente_pagamento", "pendente_assinatura", "alocado"],
}


class LanceFixoOpcaoIn(BaseModel):
    percentual: float = Field(gt=0, le=100)
    ordem: int = Field(ge=1)
    ativo: bool = True
    observacoes: Optional[str] = None


class ContractBaseIn(BaseModel):
    administradora_id: str
    numero_cota: str
    grupo_codigo: str
    produto: Literal["imobiliario", "auto"] = "imobiliario"
    valor_carta: str
    prazo: Optional[int] = None
    forma_pagamento: Optional[str] = None
    indice_correcao: Optional[str] = None
    valor_parcela: Optional[str] = None
    parcela_reduzida: bool = False
    fgts_permitido: bool = False
    embutido_permitido: bool = False
    autorizacao_gestao: bool = False
    data_adesao: Optional[str] = None
    data_assinatura: Optional[str] = None
    numero_contrato: Optional[str] = None
    opcoes_lance_fixo: List[LanceFixoOpcaoIn] = []

    # NOVO — comissão da carta
    percentual_comissao: Decimal = Field(gt=0, le=100)
    imposto_retido_pct: Decimal = Field(default=Decimal("10.0"), ge=0, le=100)

    # NOVO — repasse do parceiro sobre a comissão
    repasse_percentual_comissao: Optional[Decimal] = Field(
        default=None, gt=0, le=100
    )

    comissao_observacoes: Optional[str] = None


class ContractFromLeadIn(ContractBaseIn):
    lead_id: str


class RegisterExistingContractIn(ContractBaseIn):
    lead_id: str
    contract_status: Literal[
        "pendente_assinatura",
        "pendente_pagamento",
        "alocado",
        "contemplado",
        "cancelado",
    ] = "alocado"
    cota_situacao: Literal["ativa", "contemplada", "cancelada"] = "ativa"
    parceiro_id: Optional[str] = None
    observacoes: Optional[str] = None


class ContractStatusUpdateIn(BaseModel):
    status: Literal[
        "pendente_assinatura",
        "pendente_pagamento",
        "alocado",
        "contemplado",
        "cancelado",
    ]
    observacao: Optional[str] = None


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


def _ensure_org_header(x_org_id: Optional[str]) -> str:
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório",
        )
    return x_org_id


def _get_lead_or_404(supa: Client, *, lead_id: str) -> Dict[str, Any]:
    lead_resp = (
        supa.table("leads")
        .select("id, org_id")
        .eq("id", lead_id)
        .single()
        .execute()
    )
    lead = getattr(lead_resp, "data", None)
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    return lead


def _ensure_lead_in_org(supa: Client, *, lead_id: str, org_id: str) -> Dict[str, Any]:
    lead = _get_lead_or_404(supa, lead_id=lead_id)
    if lead["org_id"] != org_id:
        raise HTTPException(403, "Lead pertence a outra organização")
    return lead


def _build_cota_payload(
    body: ContractBaseIn,
    *,
    org_id: str,
    lead_id: str,
    cota_status: str,
) -> Dict[str, Any]:
    valor_carta = _parse_money(body.valor_carta)
    if valor_carta is None:
        raise HTTPException(400, "Valor da carta inválido")

    valor_parcela = _parse_money(body.valor_parcela)

    return {
        "org_id": org_id,
        "lead_id": lead_id,
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
        # banco real usa status para a situação da cota
        "status": cota_status,
    }


def _create_cota(supa: Client, *, payload: Dict[str, Any]) -> Dict[str, Any]:
    cota_resp = (
        supa.table("cotas")
        .insert(payload, returning="representation")
        .execute()
    )
    if not getattr(cota_resp, "data", None):
        raise HTTPException(500, "Erro ao criar cota")
    return cota_resp.data[0]


def _save_opcoes_lance_fixo(
    supa: Client,
    *,
    org_id: str,
    cota_id: str,
    opcoes: List[LanceFixoOpcaoIn],
) -> None:
    if not opcoes:
        return

    fixo_rows = [
        {
            "org_id": org_id,
            "cota_id": cota_id,
            "percentual": op.percentual,
            "ordem": op.ordem,
            "ativo": op.ativo,
            "observacoes": op.observacoes,
        }
        for op in opcoes
    ]

    fixo_resp = (
        supa.table("cota_lance_fixo_opcoes")
        .insert(fixo_rows, returning="representation")
        .execute()
    )

    if not getattr(fixo_resp, "data", None):
        raise HTTPException(500, "Erro ao salvar opções de lance fixo")


def _build_contract_payload(
    *,
    org_id: str,
    cota_id: str,
    numero: Optional[str],
    data_assinatura: Optional[str],
    status_value: str,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "org_id": org_id,
        "deal_id": None,
        "cota_id": cota_id,
        "numero": numero,
        "data_assinatura": data_assinatura,
        "status": status_value,
    }

    if status_value == "pendente_pagamento":
        payload["data_pagamento"] = None
    elif status_value == "alocado":
        payload["data_alocacao"] = body_date_or_none(data_assinatura)
    elif status_value == "contemplado":
        payload["data_alocacao"] = body_date_or_none(data_assinatura)
        payload["data_contemplacao"] = body_date_or_none(data_assinatura)

    return payload


def body_date_or_none(raw: Optional[str]) -> Optional[str]:
    return raw or None


def _create_contract(supa: Client, *, payload: Dict[str, Any]) -> Dict[str, Any]:
    contrato_resp = (
        supa.table("contratos")
        .insert(payload, returning="representation")
        .execute()
    )
    if not getattr(contrato_resp, "data", None):
        raise HTTPException(500, "Erro ao criar contrato")
    return contrato_resp.data[0]


def _maybe_link_parceiro_to_contract(
    supa: Client,
    *,
    org_id: str,
    contract_id: str,
    parceiro_id: Optional[str],
) -> int:
    if not parceiro_id:
        return 0

    payload = {
        "org_id": org_id,
        "contrato_id": contract_id,
        "parceiro_id": parceiro_id,
        "origem": "manual_contract_register",
        "principal": True,
        "observacoes": "Vínculo criado no cadastro inicial do contrato existente",
    }

    resp = (
        supa.table("contrato_parceiros")
        .insert(payload, returning="representation")
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return len(rows)


def _setup_comissao_for_contract(
    supa: Client,
    *,
    org_id: str,
    cota_id: str,
    contrato_id: str,
    percentual_comissao: Decimal,
    parceiro_id: Optional[str],
    repasse_percentual_comissao: Optional[Decimal],
    imposto_retido_pct: Decimal,
    comissao_observacoes: Optional[str],
) -> Dict[str, Any]:
    if parceiro_id and repasse_percentual_comissao is None:
        raise HTTPException(
            status_code=400,
            detail="Quando houver parceiro, informe o percentual de repasse da comissão.",
        )

    if not parceiro_id and repasse_percentual_comissao is not None:
        raise HTTPException(
            status_code=400,
            detail="Repasse só pode existir quando houver parceiro.",
        )

    parceiros = []
    if parceiro_id and repasse_percentual_comissao is not None:
        percentual_parceiro = (
            percentual_comissao * repasse_percentual_comissao
        ) / Decimal("100")

        parceiros.append(
            CotaComissaoParceiroIn(
                parceiro_id=parceiro_id,
                percentual_parceiro=percentual_parceiro,
                imposto_retido_pct=imposto_retido_pct,
                ativo=True,
                observacoes=comissao_observacoes,
            )
        )

    config_payload = CotaComissaoConfigUpsertIn(
        percentual_total=percentual_comissao,
        base_calculo="valor_carta",
        modo="avista",
        imposto_padrao_pct=imposto_retido_pct,
        primeira_competencia_regra="mes_adesao",
        furo_meses_override=None,
        ativo=True,
        observacoes=comissao_observacoes,
        regras=[
            ComissaoRegraIn(
                ordem=1,
                tipo_evento="adesao",
                offset_meses=0,
                percentual_comissao=percentual_comissao,
                descricao="Comissão gerada no cadastro inicial da carta/contrato",
            )
        ],
        parceiros=parceiros,
    )

    config_result = upsert_config_for_cota(
        supa=supa,
        org_id=org_id,
        cota_id=cota_id,
        payload=config_payload,
    )

    lancamentos_result = generate_lancamentos_for_contrato(
        supa=supa,
        org_id=org_id,
        contrato_id=contrato_id,
        sobrescrever=True,
    )

    return {
        "config": config_result,
        "lancamentos": lancamentos_result,
    }


def _finalize_contract_creation(
    supa: Client,
    *,
    org_id: str,
    lead_id: str,
    contrato_id: str,
    opcoes_lance_fixo_count: int,
    partner_links_created: int = 0,
    sync_partner_links: bool = True,
    carteira_observacoes: str,
) -> Dict[str, Any]:
    carteira_result = ensure_carteira_cliente(
        supa=supa,
        org_id=org_id,
        lead_id=lead_id,
        origem_entrada="contrato",
        observacoes=carteira_observacoes,
    )

    partner_sync = None
    if sync_partner_links:
        partner_sync = sync_contrato_parceiros_for_contract(
            supa,
            org_id=org_id,
            contract_id=contrato_id,
            actor_id=None,
        )

    return {
        "ok": True,
        "contrato_id": contrato_id,
        "carteira": carteira_result["carteira_cliente"],
        "carteira_created": carteira_result["created"],
        "opcoes_lance_fixo_count": opcoes_lance_fixo_count,
        "partner_links_created": partner_links_created,
        "partner_sync": partner_sync,
    }


@router.post("/from-lead")
def create_contract_from_lead(
    body: ContractFromLeadIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> Dict[str, Any]:
    org_id = _ensure_org_header(x_org_id)
    _ensure_lead_in_org(supa, lead_id=body.lead_id, org_id=org_id)
    _validate_opcoes_lance_fixo(body.opcoes_lance_fixo)

    cota_payload = _build_cota_payload(
        body,
        org_id=org_id,
        lead_id=body.lead_id,
        cota_status="ativa",
    )
    cota = _create_cota(supa, payload=cota_payload)
    cota_id = cota["id"]

    _save_opcoes_lance_fixo(
        supa,
        org_id=org_id,
        cota_id=cota_id,
        opcoes=body.opcoes_lance_fixo,
    )

    contrato = _create_contract(
        supa,
        payload=_build_contract_payload(
            org_id=org_id,
            cota_id=cota_id,
            numero=body.numero_contrato,
            data_assinatura=body.data_assinatura,
            status_value="pendente_assinatura",
        ),
    )

    comissao_result = _setup_comissao_for_contract(
        supa,
        org_id=org_id,
        cota_id=cota_id,
        contrato_id=contrato["id"],
        percentual_comissao=body.percentual_comissao,
        parceiro_id=None,
        repasse_percentual_comissao=None,
        imposto_retido_pct=body.imposto_retido_pct,
        comissao_observacoes=body.comissao_observacoes,
    )

    result = _finalize_contract_creation(
        supa,
        org_id=org_id,
        lead_id=body.lead_id,
        contrato_id=contrato["id"],
        opcoes_lance_fixo_count=len(body.opcoes_lance_fixo),
        sync_partner_links=True,
        carteira_observacoes="Entrada automática na carteira ao gerar contrato",
    )

    return {
        **result,
        "cota_id": cota_id,
        "comissao": comissao_result,
        "status": contrato["status"],
        "cota_situacao": cota.get("status"),
    }


@router.post("/register-existing")
def register_existing_contract(
    body: RegisterExistingContractIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> Dict[str, Any]:
    org_id = _ensure_org_header(x_org_id)
    _ensure_lead_in_org(supa, lead_id=body.lead_id, org_id=org_id)
    _validate_opcoes_lance_fixo(body.opcoes_lance_fixo)

    cota_payload = _build_cota_payload(
        body,
        org_id=org_id,
        lead_id=body.lead_id,
        cota_status=body.cota_situacao,
    )
    cota = _create_cota(supa, payload=cota_payload)
    cota_id = cota["id"]

    _save_opcoes_lance_fixo(
        supa,
        org_id=org_id,
        cota_id=cota_id,
        opcoes=body.opcoes_lance_fixo,
    )

    contrato = _create_contract(
        supa,
        payload=_build_contract_payload(
            org_id=org_id,
            cota_id=cota_id,
            numero=body.numero_contrato,
            data_assinatura=body.data_assinatura,
            status_value=body.contract_status,
        ),
    )

    comissao_result = _setup_comissao_for_contract(
        supa,
        org_id=org_id,
        cota_id=cota_id,
        contrato_id=contrato["id"],
        percentual_comissao=body.percentual_comissao,
        parceiro_id=body.parceiro_id,
        repasse_percentual_comissao=body.repasse_percentual_comissao,
        imposto_retido_pct=body.imposto_retido_pct,
        comissao_observacoes=body.comissao_observacoes,
    )

    partner_links_created = _maybe_link_parceiro_to_contract(
        supa,
        org_id=org_id,
        contract_id=contrato["id"],
        parceiro_id=body.parceiro_id,
    )

    result = _finalize_contract_creation(
        supa,
        org_id=org_id,
        lead_id=body.lead_id,
        contrato_id=contrato["id"],
        opcoes_lance_fixo_count=len(body.opcoes_lance_fixo),
        partner_links_created=partner_links_created,
        sync_partner_links=True,
        carteira_observacoes=body.observacoes or "Entrada automática na carteira ao cadastrar contrato já existente",
    )

    return {
        **result,
        "cota_id": cota_id,
        "comissao": comissao_result,
        "status": contrato["status"],
        "cota_situacao": cota.get("status"),
    }


@router.patch("/{contract_id}/status")
def update_contract_status(
    contract_id: str,
    body: ContractStatusUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _ensure_org_header(x_org_id)

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

    if contrato["org_id"] != org_id:
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

    allowed_next = TRANSICOES_VALIDAS.get(atual_status, [])
    allowed_fix = CORRECOES_VALIDAS.get(atual_status, [])

    if novo_status not in allowed_next and novo_status not in allowed_fix:
        raise HTTPException(400, f"Transição inválida: {atual_status} → {novo_status}")

    _ = (
        supa.table("contratos")
        .update({"status": novo_status})
        .eq("id", contract_id)
        .execute()
    )

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
    lead_stage_target = None

    if novo_status == "alocado":
        lead_stage_target = "ativo"
    elif novo_status == "cancelado":
        lead_stage_target = "perdido"

    if lead_stage_target:
        result = move_lead_stage(
            org_id=org_id,
            lead_id=lead_id,
            new_stage=lead_stage_target,  # type: ignore[arg-type]
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
