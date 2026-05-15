from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ImportProduto = Literal["imobiliario", "auto"]
ImportRowStatus = Literal["pronta", "aviso", "erro", "ignorada"]
ImportLanceTipo = Literal["livre", "fixo"]
ImportContemplacaoMotivo = Literal["lance", "sorteio", "outro"]


class CarteiraImportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=1)
    produto_padrao: ImportProduto = "imobiliario"


class CarteiraImportConfirmRequest(CarteiraImportPreviewRequest):
    pass


class CarteiraImportPlannedEntities(BaseModel):
    cliente_encontrado: bool = False
    cliente_criar: bool = False
    administradora_criar: bool = False
    grupo_criar: bool = False
    cota_criar: bool = False
    contrato_criar: bool = False
    lance_criar: bool = False
    contemplacao_criar: bool = False


class CarteiraImportRowPreview(BaseModel):
    row_number: int
    status: ImportRowStatus
    cliente_nome: Optional[str] = None
    administradora_nome: Optional[str] = None
    grupo_codigo: Optional[str] = None
    numero_cota: Optional[str] = None
    contrato_numero: Optional[str] = None
    lance_tipo: Optional[ImportLanceTipo] = None
    contemplada: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    planned: CarteiraImportPlannedEntities = Field(default_factory=CarteiraImportPlannedEntities)


class CarteiraImportPreviewSummary(BaseModel):
    total_rows: int = 0
    prontas: int = 0
    avisos: int = 0
    erros: int = 0
    ignoradas: int = 0
    clientes_encontrados: int = 0
    clientes_a_criar: int = 0
    administradoras_a_criar: int = 0
    grupos_a_criar: int = 0
    cotas_a_criar: int = 0
    contratos_a_criar: int = 0
    lances_a_criar: int = 0
    contemplacoes_a_criar: int = 0


class CarteiraImportPreviewResponse(BaseModel):
    ok: bool = True
    rows: list[CarteiraImportRowPreview]
    summary: CarteiraImportPreviewSummary


class CarteiraImportRowResult(BaseModel):
    row_number: int
    status: ImportRowStatus
    cliente_nome: Optional[str] = None
    lead_id: Optional[str] = None
    administradora_id: Optional[str] = None
    grupo_id: Optional[str] = None
    cota_id: Optional[str] = None
    contrato_id: Optional[str] = None
    lance_id: Optional[str] = None
    contemplacao_id: Optional[str] = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CarteiraImportConfirmResponse(BaseModel):
    ok: bool = True
    imported_rows: int = 0
    failed_rows: int = 0
    ignored_rows: int = 0
    rows: list[CarteiraImportRowResult]
    summary: CarteiraImportPreviewSummary


class ParsedImportRow(BaseModel):
    row_number: int
    cliente_nome: Optional[str] = None
    optin: Optional[bool] = None
    contemplada: bool = False
    lance_feito: bool = False
    lance_tipo: Optional[Literal["livre", "fixo", "sorteio"]] = None
    administradora_nome: Optional[str] = None
    grupo_codigo: Optional[str] = None
    numero_cota: Optional[str] = None
    produto: ImportProduto
    valor_carta: Optional[Decimal] = None
    prazo: Optional[int] = None
    forma_pagamento: Optional[str] = None
    indice_correcao: Optional[str] = None
    furo_meses: Optional[int] = None
    objetivo: Optional[str] = None
    estrategia: Optional[str] = None
    parcela_reduzida: Optional[bool] = None
    data_ultimo_lance: Optional[str] = None
    detalhes_lance: Optional[str] = None
    aporte: Optional[Decimal] = None
    valor_final_carta: Optional[Decimal] = None
    valor_parcela: Optional[Decimal] = None
    percentual_lance: Optional[Decimal] = None
    valor_lance: Optional[Decimal] = None
    numero_contrato: Optional[str] = None
    data_adesao: Optional[str] = None
    data_assinatura: Optional[str] = None
    contemplacao_motivo: ImportContemplacaoMotivo = "outro"
    observacoes_importacao: list[str] = Field(default_factory=list)
