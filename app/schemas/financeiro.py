from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


PagamentoStatus = Literal["previsto", "emitido", "pago", "atrasado", "cancelado"]
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


class PagamentoListResponse(BaseModel):
    ok: bool = True
    items: list[PagamentoOut] = Field(default_factory=list)
    total: int = 0


class FinanceiroContratoOption(BaseModel):
    contrato_id: str
    contrato_numero: Optional[str] = None
    contrato_status: Optional[str] = None
    cota_id: str
    numero_cota: Optional[str] = None
    grupo_codigo: Optional[str] = None
    valor_carta: Optional[Decimal] = None
    cliente_nome: Optional[str] = None
    possui_comissao_ativa: bool = False


class FinanceiroContratoOptionsResponse(BaseModel):
    ok: bool = True
    items: list[FinanceiroContratoOption] = Field(default_factory=list)
