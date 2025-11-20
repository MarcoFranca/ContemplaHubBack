from __future__ import annotations

from typing import List, Optional, Literal, Any
from pydantic import BaseModel, Field


class ProposalClientInfo(BaseModel):
    """
    Infos básicas do cliente embutidas no payload,
    para o PDF / página pública não depender de join.
    """
    lead_id: str
    nome: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None
    origem: Optional[str] = None


ProdutoTipo = Literal["imobiliario", "auto", "outro"]


class ProposalScenario(BaseModel):
    """
    Um cenário de carta dentro da proposta.
    Aqui a gente coloca tudo que vai aparecer para o cliente.
    """
    id: str = Field(..., description="Rótulo interno: 'A', 'B', 'C'…")
    titulo: str = Field(..., description="Ex.: Carta única 500k com redutor")
    produto: ProdutoTipo = "imobiliario"

    administradora: Optional[str] = None   # Porto, Embracon, etc.
    valor_carta: float                     # 500000
    prazo_meses: int                       # 200
    com_redutor: Optional[bool] = None

    parcela_cheia: Optional[float] = None  # sem redutor
    parcela_reduzida: Optional[float] = None  # com redutor (se houver)
    taxa_admin_anual: Optional[float] = None   # em %

    observacoes: Optional[str] = None      # notas que irão no PDF


class ProposalMeta(BaseModel):
    """
    Campos auxiliares de contexto da proposta.
    """
    campanha: Optional[str] = None
    comentario_consultor: Optional[str] = None
    validade_dias: Optional[int] = 7


class LeadProposalPayload(BaseModel):
    """
    Estrutura que vai dentro da coluna JSONB `payload`.
    """
    cliente: ProposalClientInfo
    propostas: List[ProposalScenario]
    meta: Optional[ProposalMeta] = None
    extras: Optional[dict[str, Any]] = None


# --- DTOs de entrada/saída do serviço / API ---


class CreateProposalScenarioInput(BaseModel):
    """
    O que o front manda para cada cenário ao criar proposta.
    Essencialmente igual ao ProposalScenario, mas sem forçar todos
    os campos opcionais.
    """
    id: str
    titulo: str
    produto: ProdutoTipo = "imobiliario"

    administradora: Optional[str] = None
    valor_carta: float
    prazo_meses: int
    com_redutor: Optional[bool] = None

    parcela_cheia: Optional[float] = None
    parcela_reduzida: Optional[float] = None
    taxa_admin_anual: Optional[float] = None

    observacoes: Optional[str] = None


class CreateLeadProposalInput(BaseModel):
    """
    Payload principal que o front envia para criar uma proposta.
    """
    titulo: str
    campanha: Optional[str] = None
    status: Literal["rascunho", "enviado"] = "rascunho"

    cliente_overrides: Optional[dict[str, Any]] = None
    meta: Optional[ProposalMeta] = None

    cenarios: List[CreateProposalScenarioInput]


class LeadProposalRecord(BaseModel):
    """
    Espelho da linha da tabela lead_propostas (para resposta).
    """
    id: str
    org_id: str
    lead_id: str

    titulo: Optional[str] = None
    campanha: Optional[str] = None
    status: Optional[str] = None

    public_hash: Optional[str] = None
    payload: LeadProposalPayload

    pdf_url: Optional[str] = None

    created_at: Optional[str] = None
    created_by: Optional[str] = None
    updated_at: Optional[str] = None
