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

    created_at: Optional[str] = None  # supabase retorna ISO; pydantic parseia
    first_contact_at: Optional[str] = None

    # Interesse aberto mais recente (lead_interesses)
    interest: Optional[Interest] = None

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
