from __future__ import annotations

from decimal import Decimal
from datetime import date, datetime
from typing import Literal, Optional, Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


CotaStatus = Literal["ativa", "contemplada", "cancelada"]
StatusMes = Literal["pendente", "planejado", "feito", "sem_lance", "contemplada", "cancelada"]
LanceTipo = Literal["livre", "fixo"]
LanceBaseCalculo = Literal["saldo_devedor", "valor_carta"]
ContemplacaoMotivo = Literal["lance", "sorteio", "outro"]
Produto = Literal["imobiliario", "auto"]


class CotaLanceFixoOpcaoUpdateIn(BaseModel):
    id: Optional[UUID] = None
    percentual: Decimal
    ordem: int
    ativo: bool = True
    observacoes: Optional[str] = None


class AtualizarCartaPayload(BaseModel):
    grupo_codigo: Optional[str] = None
    numero_cota: Optional[str] = None
    produto: Optional[Produto] = None
    valor_carta: Optional[Decimal] = None
    valor_parcela: Optional[Decimal] = None
    prazo: Optional[int] = None
    assembleia_dia: Optional[int] = Field(default=None, ge=1, le=31)
    data_adesao: Optional[date] = None
    autorizacao_gestao: Optional[bool] = None
    embutido_permitido: Optional[bool] = None
    embutido_max_percent: Optional[Decimal] = None
    fgts_permitido: Optional[bool] = None
    tipo_lance_preferencial: Optional[Literal["livre", "fixo"]] = None
    estrategia: Optional[str] = None
    objetivo: Optional[str] = None
    opcoes_lance_fixo: Optional[list[CotaLanceFixoOpcaoUpdateIn]] = None

    @model_validator(mode="after")
    def validate_non_empty_payload(self) -> "AtualizarCartaPayload":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo para atualização")
        return self


class CotaLanceFixoOpcaoOut(BaseModel):
    id: UUID
    cota_id: UUID
    percentual: Decimal
    ordem: int
    ativo: bool
    observacoes: Optional[str] = None
    created_at: Optional[datetime] = None


class ControleMensalPayload(BaseModel):
    competencia: date
    status_mes: Literal["pendente", "planejado", "sem_lance"]
    observacoes: Optional[str] = None


class RegistrarLancePayload(BaseModel):
    competencia: date
    assembleia_data: date
    tipo: LanceTipo
    percentual: Optional[Decimal] = None
    valor: Optional[Decimal] = None
    base_calculo: LanceBaseCalculo = "saldo_devedor"
    pagamento: Optional[dict[str, Any]] = None
    resultado: Optional[str] = None
    observacoes_competencia: Optional[str] = None
    cota_lance_fixo_opcao_id: Optional[UUID] = None


class AtualizarResultadoLancePayload(BaseModel):
    resultado: Literal["pendente", "contemplado", "nao_contemplado", "cancelado", "desconsiderado"]


class ContemplarCotaPayload(BaseModel):
    data: date
    motivo: ContemplacaoMotivo
    lance_percentual: Optional[Decimal] = None
    competencia: date


class CancelarCotaPayload(BaseModel):
    competencia: date
    observacoes: Optional[str] = None


class RegraOperadoraCreatePayload(BaseModel):
    administradora_id: UUID
    produto: Optional[Produto] = None
    dia_base_assembleia: int = Field(ge=1, le=31)
    ajustar_fim_semana: bool = True
    tipo_ajuste: Literal["proximo_dia_util", "dia_util_anterior", "sem_ajuste"] = "proximo_dia_util"
    observacoes: Optional[str] = None


class RegraOperadoraUpdatePayload(BaseModel):
    produto: Optional[Produto] = None
    dia_base_assembleia: Optional[int] = Field(default=None, ge=1, le=31)
    ajustar_fim_semana: Optional[bool] = None
    tipo_ajuste: Optional[Literal["proximo_dia_util", "dia_util_anterior", "sem_ajuste"]] = None
    observacoes: Optional[str] = None


class ControleMesOut(BaseModel):
    id: Optional[UUID] = None
    competencia: date
    status_mes: StatusMes
    lance_id: Optional[UUID] = None
    observacoes: Optional[str] = None


class UltimoLanceOut(BaseModel):
    id: UUID
    assembleia_data: Optional[date] = None
    tipo: Optional[str] = None
    percentual: Optional[Decimal] = None
    valor: Optional[Decimal] = None
    origem: Optional[str] = None
    resultado: Optional[str] = None


class RegraAssembleiaOut(BaseModel):
    origem: Optional[str] = None
    produto: Optional[str] = None
    dia_base_assembleia: Optional[int] = None
    ajustar_fim_semana: Optional[bool] = None
    tipo_ajuste: Optional[str] = None
    assembleia_prevista: Optional[date] = None


class LanceCartaListItem(BaseModel):
    cota_id: UUID
    lead_id: Optional[UUID] = None
    cliente_nome: Optional[str] = None
    administradora_id: Optional[UUID] = None
    administradora_nome: Optional[str] = None
    produto: str
    grupo_codigo: str
    numero_cota: str
    valor_carta: Optional[Decimal] = None
    valor_parcela: Optional[Decimal] = None
    prazo: Optional[int] = None
    status: str
    autorizacao_gestao: bool
    embutido_permitido: bool
    embutido_max_percent: Optional[Decimal] = None
    fgts_permitido: bool
    tipo_lance_preferencial: Optional[str] = None
    estrategia: Optional[str] = None
    assembleia_dia_origem: Optional[str] = None
    assembleia_dia: Optional[int] = None
    assembleia_prevista: Optional[date] = None
    competencia: date
    status_mes: StatusMes
    tem_pendencia_configuracao: bool
    opcoes_lance_fixo: list[CotaLanceFixoOpcaoOut] = []
    debug_fixo: Optional[str] = None


class LanceCartaListResponse(BaseModel):
    items: list[LanceCartaListItem]
    page: int
    page_size: int
    total: int


class LancesCartaDetalheOut(BaseModel):
    cota: dict[str, Any]
    lead: Optional[dict[str, Any]] = None
    administradora: Optional[dict[str, Any]] = None
    regra_assembleia: RegraAssembleiaOut
    controle_mes_atual: ControleMesOut
    historico_lances: list[dict[str, Any]]
    contemplacao: Optional[dict[str, Any]] = None
    diagnostico: Optional[dict[str, Any]] = None
    opcoes_lance_fixo: list[CotaLanceFixoOpcaoOut] = []

class SimpleOkResponse(BaseModel):
    ok: bool = True


class RegraOperadoraOut(BaseModel):
    id: UUID
    org_id: UUID
    administradora_id: UUID
    administradora_nome: Optional[str] = None
    produto: Optional[str] = None
    dia_base_assembleia: int
    ajustar_fim_semana: bool
    tipo_ajuste: str
    observacoes: Optional[str] = None
    created_at: Optional[datetime] = None