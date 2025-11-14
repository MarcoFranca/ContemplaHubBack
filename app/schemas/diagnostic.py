# app/schemas/diagnostic.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, Any, Dict

from pydantic import BaseModel, Field


class DiagnosticInput(BaseModel):
    # Objetivo & contexto
    objetivo: Optional[str] = None
    prazo_meta_meses: Optional[int] = Field(default=None, ge=0)
    preferencia_produto: Optional[str] = None
    regiao_preferencia: Optional[str] = None

    # Capacidade financeira
    renda_mensal: Optional[float] = Field(default=None, ge=0)
    reserva_inicial: Optional[float] = Field(default=None, ge=0)
    comprometimento_max_pct: Optional[float] = Field(
        default=None, ge=0, le=100
    )
    renda_provada: Optional[bool] = False

    # Configuração da carta-alvo
    valor_carta_alvo: Optional[float] = Field(default=None, ge=0)
    prazo_alvo_meses: Optional[int] = Field(default=None, ge=0)

    # Estratégia de lance
    estrategia_lance: Optional[str] = None
    lance_base_pct: Optional[float] = Field(default=None, ge=0)
    lance_max_pct: Optional[float] = Field(default=None, ge=0)
    janela_preferida_semanas: Optional[int] = Field(default=None, ge=0)

    # Extras (para perguntas livres, versões futuras, etc.)
    extras: Optional[Dict[str, Any]] = None

    # LGPD (opcional por enquanto)
    consent_scope: Optional[str] = None


class DiagnosticScores(BaseModel):
    score_risco: int
    readiness_score: int
    prob_conversao: float
    prob_contemplacao_short: float
    prob_contemplacao_med: float
    prob_contemplacao_long: float


class DiagnosticRecord(BaseModel):
    # espelho da tabela lead_diagnosticos, simplificado
    id: Optional[str] = None
    org_id: str
    lead_id: str

    # Principais campos salvos
    objetivo: Optional[str] = None
    prazo_meta_meses: Optional[int] = None
    preferencia_produto: Optional[str] = None
    regiao_preferencia: Optional[str] = None

    renda_mensal: Optional[float] = None
    reserva_inicial: Optional[float] = None
    comprometimento_max_pct: Optional[float] = None
    renda_provada: Optional[bool] = None

    valor_carta_alvo: Optional[float] = None
    prazo_alvo_meses: Optional[int] = None

    estrategia_lance: Optional[str] = None
    lance_base_pct: Optional[float] = None
    lance_max_pct: Optional[float] = None
    janela_preferida_semanas: Optional[int] = None

    score_risco: Optional[int] = None
    readiness_score: Optional[int] = None
    prob_conversao: Optional[float] = None
    prob_contemplacao_short: Optional[float] = None
    prob_contemplacao_med: Optional[float] = None
    prob_contemplacao_long: Optional[float] = None

    consent_scope: Optional[str] = None
    consent_ts: Optional[datetime] = None

    extras: Optional[Dict[str, Any]] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DiagnosticResponse(BaseModel):
    lead_id: str
    org_id: str
    scores: DiagnosticScores
    record: DiagnosticRecord

