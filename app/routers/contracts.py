from __future__ import annotations

from typing import Optional, Literal, Dict, Any, List

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, ConfigDict, AliasChoices, model_validator
from supabase import Client
from postgrest.exceptions import APIError

from app.routers.carteira import ensure_carteira_cliente
from app.deps import get_supabase_admin
from app.services.contract_partner_sync_service import sync_contrato_parceiros_for_contract
from app.services.cota_finance_service import normalize_cota_financial_payload
from app.services.kanban_service import move_lead_stage
from decimal import Decimal

from app.schemas.comissoes import (
    CotaComissaoConfigUpsertIn,
    CotaComissaoParceiroIn,
    ComissaoRegraIn,
)
from app.services.comissao_service import (
    fetch_config_by_cota,
    fetch_parceiros_da_cota,
    fetch_regras,
    upsert_config_for_cota,
)
from app.services.comissao_competencia_service import reprocessar_comissoes_contrato

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
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    administradora_id: str
    numero_cota: str
    grupo_codigo: str = Field(
        validation_alias=AliasChoices("grupo_codigo", "grupo"),
    )
    produto: Literal["imobiliario", "auto"] = "imobiliario"
    valor_carta: str
    prazo: Optional[int] = None
    forma_pagamento: Optional[str] = None
    indice_correcao: Optional[str] = None
    valor_parcela: Optional[str] = None
    fundo_reserva_percentual: Optional[str] = None
    fundo_reserva_valor_mensal: Optional[str] = None
    seguro_prestamista_ativo: bool = False
    seguro_prestamista_percentual: Optional[str] = None
    seguro_prestamista_valor_mensal: Optional[str] = None
    taxa_admin_antecipada_ativo: bool = False
    taxa_admin_antecipada_percentual: Optional[str] = None
    taxa_admin_antecipada_forma_pagamento: Optional[Literal["avista", "parcelado"]] = None
    taxa_admin_antecipada_parcelas: Optional[int] = None
    taxa_admin_antecipada_valor_total: Optional[str] = None
    taxa_admin_antecipada_valor_parcela: Optional[str] = None
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
    existing_cota_id: Optional[str] = None
    prazo: int
    valor_parcela: str
    data_adesao: str
    numero_contrato: str
    data_assinatura: str
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

    @model_validator(mode="after")
    def validate_initial_state_integrity(self) -> "RegisterExistingContractIn":
        if (
            self.contract_status == "contemplado"
            and self.cota_situacao != "contemplada"
        ):
            raise ValueError(
                "Contrato contemplado exige cota em situação contemplada."
            )

        if (
            self.contract_status in {"pendente_assinatura", "pendente_pagamento"}
            and self.cota_situacao == "cancelada"
        ):
            raise ValueError(
                "Contrato pendente não pode nascer com cota cancelada."
            )

        return self


class ContractStatusUpdateIn(BaseModel):
    status: Literal[
        "pendente_assinatura",
        "pendente_pagamento",
        "alocado",
        "contemplado",
        "cancelado",
    ]
    observacao: Optional[str] = None


class ContractDadosUpdateIn(BaseModel):
    numero: Optional[str] = None
    data_assinatura: Optional[str] = None

    @model_validator(mode="after")
    def validate_non_empty_payload(self) -> "ContractDadosUpdateIn":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo para atualização")
        return self


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

    return normalize_cota_financial_payload({
        "org_id": org_id,
        "lead_id": lead_id,
        "administradora_id": body.administradora_id,
        "numero_cota": body.numero_cota,
        "grupo_codigo": body.grupo_codigo,
        "produto": body.produto,
        "valor_carta": valor_carta,
        "valor_parcela": valor_parcela,
        "fundo_reserva_percentual": body.fundo_reserva_percentual,
        "fundo_reserva_valor_mensal": body.fundo_reserva_valor_mensal,
        "seguro_prestamista_ativo": body.seguro_prestamista_ativo,
        "seguro_prestamista_percentual": body.seguro_prestamista_percentual,
        "seguro_prestamista_valor_mensal": body.seguro_prestamista_valor_mensal,
        "taxa_admin_antecipada_ativo": body.taxa_admin_antecipada_ativo,
        "taxa_admin_antecipada_percentual": body.taxa_admin_antecipada_percentual,
        "taxa_admin_antecipada_forma_pagamento": body.taxa_admin_antecipada_forma_pagamento,
        "taxa_admin_antecipada_parcelas": body.taxa_admin_antecipada_parcelas,
        "taxa_admin_antecipada_valor_total": body.taxa_admin_antecipada_valor_total,
        "taxa_admin_antecipada_valor_parcela": body.taxa_admin_antecipada_valor_parcela,
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
    })    


def _ensure_administradora_exists(supa: Client, *, administradora_id: str) -> None:
    resp = (
        supa.table("administradoras")
        .select("id")
        .eq("id", administradora_id)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(404, "Administradora não encontrada")


def _ensure_administradora_in_org(
    supa: Client,
    *,
    administradora_id: str,
    org_id: str,
) -> None:
    resp = (
        supa.table("administradoras")
        .select("id, org_id")
        .eq("id", administradora_id)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(404, "Administradora não encontrada")

    administradora = rows[0]
    administradora_org_id = administradora.get("org_id")

    if administradora_org_id == org_id:
        return

    if administradora_org_id in (None, "", "global", "GLOBAL"):
        return

    raise HTTPException(
        403,
        "Administradora inválida para a organização informada",
    )


def _ensure_parceiro_in_org(
    supa: Client,
    *,
    parceiro_id: Optional[str],
    org_id: str,
) -> None:
    if not parceiro_id:
        return

    resp = (
        supa.table("parceiros_corretores")
        .select("id, org_id")
        .eq("id", parceiro_id)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(404, "Parceiro não encontrado")

    parceiro = rows[0]
    if parceiro.get("org_id") != org_id:
        raise HTTPException(403, "Parceiro pertence a outra organização")


def _create_cota(supa: Client, *, payload: Dict[str, Any]) -> Dict[str, Any]:
    cota_resp = (
        supa.table("cotas")
        .insert(payload, returning="representation")
        .execute()
    )
    if not getattr(cota_resp, "data", None):
        raise HTTPException(500, "Erro ao criar cota")
    return cota_resp.data[0]


def _ensure_cota_in_org(
    supa: Client,
    *,
    cota_id: str,
    org_id: str,
    lead_id: str,
) -> Dict[str, Any]:
    resp = (
        supa.table("cotas")
        .select("id, org_id, lead_id")
        .eq("id", cota_id)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(404, "Cota não encontrada")

    cota = rows[0]
    if cota.get("org_id") != org_id:
        raise HTTPException(403, "Cota pertence a outra organização")
    if cota.get("lead_id") != lead_id:
        raise HTTPException(403, "Cota pertence a outro lead")
    return cota


def _update_cota(
    supa: Client, *, cota_id: str, org_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    cota_resp = (
        supa.table("cotas")
        .update(payload, returning="representation")
        .eq("id", cota_id)
        .eq("org_id", org_id)
        .execute()
    )
    rows = getattr(cota_resp, "data", None) or []
    if not rows:
        raise HTTPException(500, "Erro ao atualizar cota")
    return rows[0]


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

    # Upsert por (cota_id, ordem): atualiza a opção existente da mesma ordem
    # ou inclui uma nova, sem duplicar nem apagar as demais já cadastradas.
    fixo_resp = (
        supa.table("cota_lance_fixo_opcoes")
        .upsert(fixo_rows, on_conflict="cota_id,ordem", returning="representation")
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
    infer_operational_dates: bool = False,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "org_id": org_id,
        "deal_id": None,
        "cota_id": cota_id,
        "numero": numero,
        "data_assinatura": data_assinatura,
        "status": status_value,
    }

    if not infer_operational_dates:
        return payload

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

    # Idempotente: o vínculo pode já existir (trigger/fluxo anterior). A constraint
    # contrato_parceiros_unique (org_id, contrato_id, parceiro_id) faria o insert
    # estourar 23505 e derrubar todo o cadastro com 500.
    existing = (
        supa.table("contrato_parceiros")
        .select("id")
        .eq("org_id", org_id)
        .eq("contrato_id", contract_id)
        .eq("parceiro_id", parceiro_id)
        .limit(1)
        .execute()
    )
    if getattr(existing, "data", None):
        return 0

    payload = {
        "org_id": org_id,
        "contrato_id": contract_id,
        "parceiro_id": parceiro_id,
        "origem": "manual_contract_register",
        "principal": True,
        "observacoes": "Vínculo criado no cadastro inicial do contrato existente",
    }

    try:
        resp = (
            supa.table("contrato_parceiros")
            .insert(payload, returning="representation")
            .execute()
        )
    except APIError as exc:
        # Corrida: outro processo criou o vínculo entre o select e o insert.
        if getattr(exc, "code", None) == "23505":
            return 0
        raise

    rows = getattr(resp, "data", None) or []
    return len(rows)


def _ensure_no_duplicate_operational_registration(
    supa: Client,
    *,
    org_id: str,
    lead_id: str,
    administradora_id: str,
    grupo_codigo: str,
    numero_cota: str,
    numero_contrato: str,
    exclude_cota_id: Optional[str] = None,
) -> None:
    contrato_resp = (
        supa.table("contratos")
        .select("id, numero, cota_id")
        .eq("org_id", org_id)
        .eq("numero", numero_contrato)
        .limit(1)
        .execute()
    )
    contratos = getattr(contrato_resp, "data", None) or []
    if contratos:
        raise HTTPException(
            409,
            "Já existe contrato com esse número na organização.",
        )

    cota_resp = (
        supa.table("cotas")
        .select("id, lead_id, administradora_id, grupo_codigo, numero_cota")
        .eq("org_id", org_id)
        .eq("administradora_id", administradora_id)
        .eq("grupo_codigo", grupo_codigo)
        .eq("numero_cota", numero_cota)
        .limit(1)
        .execute()
    )
    cotas = getattr(cota_resp, "data", None) or []
    if cotas:
        existing = cotas[0]
        if exclude_cota_id and existing.get("id") == exclude_cota_id:
            return
        if existing.get("lead_id") == lead_id:
            raise HTTPException(
                409,
                "Já existe cota cadastrada para este lead com a mesma administradora, grupo e número.",
            )
        raise HTTPException(
            409,
            "Já existe cota cadastrada na organização com a mesma administradora, grupo e número.",
        )


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

    lancamentos_result = reprocessar_comissoes_contrato(
        supa=supa,
        org_id=org_id,
        contrato_id=contrato_id,
        actor_id=None,
    )

    return {
        "config": config_result,
        "lancamentos": lancamentos_result,
    }


def _ensure_comissao_config_for_contract(
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
    existing_config = fetch_config_by_cota(supa, org_id, cota_id)
    if existing_config:
        config_result: Dict[str, Any] = {
            "ok": True,
            "config": existing_config,
            "regras": fetch_regras(supa, org_id, existing_config["id"]),
            "parceiros": fetch_parceiros_da_cota(supa, org_id, cota_id),
            "initialized_from_contract": False,
        }
        lancamentos_result = reprocessar_comissoes_contrato(
            supa=supa,
            org_id=org_id,
            contrato_id=contrato_id,
            actor_id=None,
        )
        return {
            "config": config_result,
            "lancamentos": lancamentos_result,
        }

    result = _setup_comissao_for_contract(
        supa,
        org_id=org_id,
        cota_id=cota_id,
        contrato_id=contrato_id,
        percentual_comissao=percentual_comissao,
        parceiro_id=parceiro_id,
        repasse_percentual_comissao=repasse_percentual_comissao,
        imposto_retido_pct=imposto_retido_pct,
        comissao_observacoes=comissao_observacoes,
    )
    result["config"]["initialized_from_contract"] = True
    return result


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
    _ensure_administradora_exists(supa, administradora_id=body.administradora_id)
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
            infer_operational_dates=False,
        ),
    )

    comissao_result = _ensure_comissao_config_for_contract(
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
        "contract_status": contrato["status"],
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
    _ensure_administradora_in_org(
        supa,
        administradora_id=body.administradora_id,
        org_id=org_id,
    )
    _ensure_parceiro_in_org(supa, parceiro_id=body.parceiro_id, org_id=org_id)
    _validate_opcoes_lance_fixo(body.opcoes_lance_fixo)

    if body.existing_cota_id:
        _ensure_cota_in_org(
            supa,
            cota_id=body.existing_cota_id,
            org_id=org_id,
            lead_id=body.lead_id,
        )

    _ensure_no_duplicate_operational_registration(
        supa,
        org_id=org_id,
        lead_id=body.lead_id,
        administradora_id=body.administradora_id,
        grupo_codigo=body.grupo_codigo,
        numero_cota=body.numero_cota,
        numero_contrato=body.numero_contrato,
        exclude_cota_id=body.existing_cota_id,
    )

    cota_payload = _build_cota_payload(
        body,
        org_id=org_id,
        lead_id=body.lead_id,
        cota_status=body.cota_situacao,
    )

    if body.existing_cota_id:
        cota = _update_cota(
            supa,
            cota_id=body.existing_cota_id,
            org_id=org_id,
            payload=cota_payload,
        )
    else:
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
            infer_operational_dates=False,
        ),
    )

    comissao_result = _ensure_comissao_config_for_contract(
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
        "contract_status": contrato["status"],
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

    if novo_status in {"alocado", "contemplado"}:
        lead_stage_target = "pos_venda"
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


@router.patch("/{contract_id}/dados")
def update_contract_dados(
    contract_id: str,
    body: ContractDadosUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _ensure_org_header(x_org_id)

    resp = (
        supa.table("contratos")
        .select("id, org_id")
        .eq("id", contract_id)
        .single()
        .execute()
    )
    contrato = getattr(resp, "data", None)

    if not contrato:
        raise HTTPException(404, "Contrato não encontrado")

    if contrato["org_id"] != org_id:
        raise HTTPException(403, "Contrato pertence a outra organização")

    fields = body.model_fields_set
    update_payload: Dict[str, Any] = {}

    if "numero" in fields:
        update_payload["numero"] = body.numero

    if "data_assinatura" in fields:
        update_payload["data_assinatura"] = body.data_assinatura

    (
        supa.table("contratos")
        .update(update_payload)
        .eq("id", contract_id)
        .execute()
    )

    return {"ok": True, "contrato_id": contract_id}
