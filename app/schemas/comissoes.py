from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


ComissaoModo = Literal["avista", "parcelado"]
PrimeiraCompetenciaRegra = Literal["mes_adesao", "primeira_cobranca_valida", "manual"]
ComissaoEvento = Literal[
    "adesao",
    "primeira_cobranca_valida",
    "proxima_cobranca",
    "contemplacao",
    "manual",
]
BeneficiarioTipo = Literal["empresa", "parceiro"]
ComissaoStatus = Literal["previsto", "disponivel", "pago", "cancelado"]
RepasseStatus = Literal["nao_aplicavel", "pendente", "pago", "cancelado"]
PixTipo = Literal["cpf", "cnpj", "email", "telefone", "aleatoria"]


class ParceiroCreateIn(BaseModel):
    nome: str = Field(min_length=2)
    cpf_cnpj: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None
    pix_tipo: Optional[PixTipo] = None
    pix_chave: Optional[str] = None
    ativo: bool = True
    observacoes: Optional[str] = None


class ParceiroUpdateIn(BaseModel):
    nome: Optional[str] = Field(default=None, min_length=2)
    cpf_cnpj: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None
    pix_tipo: Optional[PixTipo] = None
    pix_chave: Optional[str] = None
    ativo: Optional[bool] = None
    observacoes: Optional[str] = None


class ParceiroAccessIn(BaseModel):
    criar_acesso: bool = False
    email_acesso: Optional[EmailStr] = None
    nome_acesso: Optional[str] = Field(default=None, min_length=2)
    telefone_acesso: Optional[str] = None
    ativo: bool = True

    can_view_client_data: bool = False
    can_view_contracts: bool = True
    can_view_commissions: bool = True

    @model_validator(mode="after")
    def validate_email_if_create_access(self) -> "ParceiroAccessIn":
        if self.criar_acesso and not self.email_acesso:
            raise ValueError("email_acesso é obrigatório quando criar_acesso=true")
        return self


class ParceiroCreateWithAccessIn(ParceiroCreateIn):
    acesso: Optional[ParceiroAccessIn] = None


class ParceiroToggleIn(BaseModel):
    ativo: bool
    disabled_reason: Optional[str] = None


class ComissaoRegraIn(BaseModel):
    ordem: int = Field(ge=1)
    tipo_evento: ComissaoEvento
    offset_meses: int = Field(default=0, ge=0)
    percentual_comissao: Decimal = Field(gt=0)
    descricao: Optional[str] = None


class CotaComissaoParceiroIn(BaseModel):
    parceiro_id: str
    percentual_parceiro: Decimal = Field(gt=0)
    imposto_retido_pct: Decimal = Field(default=Decimal("10.0"), ge=0, le=100)
    ativo: bool = True
    observacoes: Optional[str] = None


class CotaComissaoConfigUpsertIn(BaseModel):
    percentual_total: Decimal = Field(gt=0)
    base_calculo: str = "valor_carta"
    modo: ComissaoModo = "avista"
    imposto_padrao_pct: Decimal = Field(default=Decimal("10.0"), ge=0, le=100)
    primeira_competencia_regra: PrimeiraCompetenciaRegra = "mes_adesao"
    furo_meses_override: Optional[int] = Field(default=None, ge=0)
    ativo: bool = True
    observacoes: Optional[str] = None
    regras: list[ComissaoRegraIn]
    parceiros: list[CotaComissaoParceiroIn] = []

    @field_validator("regras")
    @classmethod
    def validate_regras_not_empty(cls, value: list[ComissaoRegraIn]) -> list[ComissaoRegraIn]:
        if not value:
            raise ValueError("Informe ao menos uma regra de comissão")
        return value

    @model_validator(mode="after")
    def validate_consistencia(self) -> "CotaComissaoConfigUpsertIn":
        ordens = [r.ordem for r in self.regras]
        if len(ordens) != len(set(ordens)):
            raise ValueError("Há ordens duplicadas nas regras de comissão")

        total_regras = sum((r.percentual_comissao for r in self.regras), start=Decimal("0"))
        if total_regras.quantize(Decimal("0.0001")) != self.percentual_total.quantize(Decimal("0.0001")):
            raise ValueError("A soma das regras deve ser igual ao percentual total da comissão")

        total_parceiros = sum((p.percentual_parceiro for p in self.parceiros), start=Decimal("0"))
        if total_parceiros > self.percentual_total:
            raise ValueError("A soma dos percentuais dos parceiros não pode superar a comissão total")

        if self.modo == "avista" and len(self.regras) != 1:
            raise ValueError("Comissão à vista deve possuir exatamente uma regra")

        return self


class GerarLancamentosIn(BaseModel):
    sobrescrever: bool = False


class LancamentoStatusUpdateIn(BaseModel):
    status: Literal["disponivel", "pago", "cancelado"]
    competencia_real: Optional[date] = None
    observacoes: Optional[str] = None


class RepasseUpdateIn(BaseModel):
    repasse_status: Literal["pendente", "pago", "cancelado"]
    repasse_previsto_em: Optional[date] = None
    repasse_pago_em: Optional[datetime] = None
    repasse_observacoes: Optional[str] = None


class ComissaoListFilters(BaseModel):
    parceiro_id: Optional[str] = None
    contrato_id: Optional[str] = None
    cota_id: Optional[str] = None
    status: Optional[ComissaoStatus] = None
    repasse_status: Optional[RepasseStatus] = None
    competencia_de: Optional[date] = None
    competencia_ate: Optional[date] = None
