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

    # Redutor de parcela
    com_redutor: Optional[bool] = None
    redutor_percent: Optional[float] = Field(
        default=None,
        description="Percentual de redutor de parcela (0–100). Ex.: 40 para 40%."
    )

    # Parcelas
    parcela_cheia: Optional[float] = None          # sem redutor
    parcela_reduzida: Optional[float] = None       # com redutor (se houver)

    # Taxa adm total (%) sobre a carta
    taxa_admin_anual: Optional[float] = Field(
        default=None,
        description="Taxa de administração total (%) sobre a carta."
    )

    # Fundo de reserva (% sobre a carta)
    fundo_reserva_pct: Optional[float] = Field(
        default=None,
        description="Percentual de fundo de reserva (0–100)."
    )

    # Seguro prestamista (sim/não)
    seguro_prestamista: Optional[bool] = None

    # Lances fixos (podem existir até 2)
    lance_fixo_pct_1: Optional[float] = Field(
        default=None,
        description="Percentual do primeiro lance fixo (0–100)."
    )
    lance_fixo_pct_2: Optional[float] = Field(
        default=None,
        description="Percentual do segundo lance fixo (0–100)."
    )

    # Lance embutido
    permite_lance_embutido: Optional[bool] = None
    lance_embutido_pct_max: Optional[float] = Field(
        default=None,
        description="Percentual máximo de lance embutido (0–100)."
    )

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
    """
    id: str
    titulo: str
    produto: ProdutoTipo = "imobiliario"

    administradora: Optional[str] = None
    valor_carta: float
    prazo_meses: int

    com_redutor: Optional[bool] = None
    redutor_percent: Optional[float] = None

    parcela_cheia: Optional[float] = None
    parcela_reduzida: Optional[float] = None
    taxa_admin_anual: Optional[float] = None

    fundo_reserva_pct: Optional[float] = None
    seguro_prestamista: Optional[bool] = None

    lance_fixo_pct_1: Optional[float] = None
    lance_fixo_pct_2: Optional[float] = None

    permite_lance_embutido: Optional[bool] = None
    lance_embutido_pct_max: Optional[float] = None

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
