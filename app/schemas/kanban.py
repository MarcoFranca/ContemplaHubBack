# app/schemas/kanban.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel

# Estágios possíveis do funil
Stage = Literal[
    "novo",
    "diagnostico",
    "proposta",
    "negociacao",
    "contrato",
    "ativo",
    "perdido",
]


class Interest(BaseModel):
    """
    Espelho do que o front espera em lead.interest
    """
    produto: Optional[str] = None
    valorTotal: Optional[str] = None  # string (ex.: "300000" ou "300.000,00")
    prazoMeses: Optional[int] = None
    objetivo: Optional[str] = None
    perfilDesejado: Optional[str] = None
    observacao: Optional[str] = None


class InterestInsight(BaseModel):
    score: int                       # 0–100: qualidade/força do interesse
    missing_fields: List[str]        # ["Prazo", "Objetivo", ...]
    next_best_action: str            # texto pronto pro consultor
    suggested_questions: List[str]   # 3–5 perguntas chave
    likely_objections: List[str]     # objeções prováveis (educacional)
    priority: Literal["baixa", "media", "alta"]  # combinado com diagnóstico (depois)

    strategy_ideas: Optional[List[str]] = None           # frases de estratégia
    suggested_ticket_splits: Optional[List[str]] = None  # “1x 500k” / “2x 250k”


class MetaAdsSummary(BaseModel):
    objetivo_consorcio_label: Optional[str] = None
    valor_mensal_pretendido_label: Optional[str] = None
    renda_mensal_label: Optional[str] = None
    leadgen_id: Optional[str] = None
    platform: Optional[str] = None
    campaign_name: Optional[str] = None
    adset_name: Optional[str] = None
    ad_name: Optional[str] = None
    form_name: Optional[str] = None


class LeadCard(BaseModel):
    """
    Card de lead usado nas colunas do Kanban.
    """
    id: str
    nome: str
    etapa: Stage

    telefone: Optional[str] = None
    email: Optional[str] = None
    origem: Optional[str] = None
    owner_id: Optional[str] = None
    cep: Optional[str] = None
    logradouro: Optional[str] = None
    numero: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    created_at: Optional[str] = None  # supabase retorna ISO; pydantic parseia
    first_contact_at: Optional[str] = None
    address_updated_at: Optional[str] = None
    source_label: Optional[str] = None
    form_label: Optional[str] = None
    channel: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_term: Optional[str] = None
    utm_content: Optional[str] = None

    # Interesse aberto mais recente (lead_interesses)
    interest: Optional[Interest] = None
    interest_insight: Optional[InterestInsight] = None
    meta_ads_form_answers: Optional[Dict[str, Any]] = None
    meta_ads_summary: Optional[MetaAdsSummary] = None

    # Scores do diagnóstico (lead_diagnosticos)
    readiness_score: Optional[int] = None
    score_risco: Optional[int] = None
    prob_conversao: Optional[float] = None


class KanbanSnapshot(BaseModel):
    """
    Payload principal do /kanban:
    { columns: { [stage]: LeadCard[] } }
    """
    columns: Dict[Stage, List[LeadCard]]


class KanbanMetrics(BaseModel):
    """
    Payload do /kanban/metrics.
    Campos são opcionais para aceitação flexível do RPC.
    """
    avgDays: Optional[Dict[str, float]] = None
    conversion: Optional[Dict[str, float]] = None
    diagCompletionPct: Optional[Dict[str, float]] = None
    readinessAvg: Optional[Dict[str, float]] = None
    tFirstContactAvgMin: Optional[Dict[str, float]] = None
    raw: Optional[Any] = None
