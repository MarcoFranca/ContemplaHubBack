from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


PagamentoStatus = Literal["previsto", "emitido", "pago", "atrasado", "inadimplente", "cancelado"]
PagamentoOrigem = Literal["parcela", "adesao", "comissao", "taxa", "manual"]
PagamentoTipo = Literal["parcela_mensal", "lance", "taxa", "outro"]


class PagamentoUpsertIn(BaseModel):
    contrato_id: str
    competencia: date
    valor: Decimal = Field(gt=0)
    vencimento: Optional[date] = None
    status: PagamentoStatus = "previsto"
    pago_em: Optional[datetime] = None
    observacoes: Optional[str] = None
    referencia: Optional[str] = None
    tipo: PagamentoTipo = "parcela_mensal"
    origem: PagamentoOrigem = "parcela"


class PagamentoOut(BaseModel):
    id: str
    org_id: str
    contrato_id: str
    cota_id: Optional[str] = None
    contrato_numero: Optional[str] = None
    numero_cota: Optional[str] = None
    grupo_codigo: Optional[str] = None
    cliente_nome: Optional[str] = None
    tipo: str
    competencia: Optional[date] = None
    valor: Decimal
    pago_em: Optional[datetime] = None
    created_at: Optional[datetime] = None
    status: Optional[str] = None
    vencimento: Optional[date] = None
    referencia: Optional[str] = None
    origem: Optional[str] = None
    observacoes: Optional[str] = None
    payload: dict = Field(default_factory=dict)
    competencia_id: Optional[str] = None
    competencia_status: Optional[str] = None
    gera_comissao: Optional[bool] = None
    participou_assembleia: Optional[bool] = None
    lancamentos_total: int = 0
    lancamentos_previstos: int = 0
    lancamentos_disponiveis: int = 0
    lancamentos_pagos: int = 0
    lancamentos_cancelados: int = 0
    repasses_pendentes: int = 0


class PagamentoListResponse(BaseModel):
    ok: bool = True
    items: list[PagamentoOut] = Field(default_factory=list)
    total: int = 0


class FinanceiroContratoOption(BaseModel):
    selection_id: str
    tem_contrato: bool = False
    contrato_id: str
    contrato_numero: Optional[str] = None
    contrato_status: Optional[str] = None
    cota_status: Optional[str] = None
    cota_id: str
    numero_cota: Optional[str] = None
    grupo_codigo: Optional[str] = None
    valor_carta: Optional[Decimal] = None
    cliente_nome: Optional[str] = None
    administradora_nome: Optional[str] = None
    possui_comissao_ativa: bool = False
    percentual_comissao: Optional[Decimal] = None
    modo_comissao: Optional[str] = None
    parceiro_vinculado: bool = False
    parceiro_nome: Optional[str] = None
    parceiro_percentual: Optional[Decimal] = None


class FinanceiroContratoOptionsResponse(BaseModel):
    ok: bool = True
    items: list[FinanceiroContratoOption] = Field(default_factory=list)


class ContratoNumeroUpdateIn(BaseModel):
    numero_contrato: str = Field(min_length=1, max_length=120)


class CronogramaContratoResponse(BaseModel):
    ok: bool = True
    contrato_id: str
    pagamentos_processados: int = 0
    pagamentos_criados: int = 0
    pagamentos_atualizados: int = 0
    pagamentos_cancelados: int = 0
    competencias_processadas: int = 0


class PagamentoOperacaoResponse(BaseModel):
    ok: bool = True
    pagamento_id: str
    pagamentos_afetados: int = 0
    message: Optional[str] = None
