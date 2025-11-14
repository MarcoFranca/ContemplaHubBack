# app/schemas/kanban.py
from __future__ import annotations

from datetime import datetime
from typing import Literal, Dict, List, Optional

from pydantic import BaseModel


Stage = Literal[
    "novo",
    "diagnostico",
    "proposta",
    "negociacao",
    "contrato",
    "ativo",
    "perdido",
]


class LeadCard(BaseModel):
    id: str
    nome: str
    etapa: Stage

    telefone: Optional[str] = None
    email: Optional[str] = None
    origem: Optional[str] = None
    owner_id: Optional[str] = None

    created_at: Optional[datetime] = None
    first_contact_at: Optional[datetime] = None

    # Campo aberto para o “resumo de interesse” (você pode popular depois)
    interest_summary: Optional[str] = None


class KanbanSnapshot(BaseModel):
    columns: Dict[Stage, List[LeadCard]]


class KanbanMetrics(BaseModel):
    # Estrutura pensada para casar com o <ColumnHeaderStats> no front
    avgDays: Optional[Dict[str, float]] = None
    conversion: Optional[Dict[str, float]] = None
    diagCompletionPct: Optional[Dict[str, float]] = None
    readinessAvg: Optional[Dict[str, float]] = None
    tFirstContactAvgMin: Optional[Dict[str, float]] = None

    # opcional: payload bruto vindo do banco, se quiser depurar
    raw: Optional[dict] = None
