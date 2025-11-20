from __future__ import annotations

import json
import random
import string
from typing import Any, List, Optional, Dict

from supabase import Client

from app.schemas.propostas import (
    CreateLeadProposalInput,
    LeadProposalRecord,
    LeadProposalPayload,
    ProposalClientInfo,
    ProposalScenario,
)
from app.schemas.propostas import ProposalMeta

def _get_resp_data(resp: Any) -> Any:
    """
    Helper defensivo: extrai resp.data sem quebrar se resp for None ou não tiver .data.
    Também loga o tipo de resp em caso estranho, pra debug.
    """
    if resp is None:
        print("WARN: Supabase response is None")
        return None

    data = getattr(resp, "data", None)
    if data is None:
        # Em dev, isso ajuda demais
        print("WARN: Supabase response without data. Full resp:", resp)
    return data


def _normalize_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    raise TypeError(f"Payload em formato inesperado: {type(raw)}")


def _random_hash(length: int = 7) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def _generate_unique_public_hash(supa: Client) -> str:
    """
    Gera um hash curto e garante que não existe outra lead_proposta com ele.
    """
    for _ in range(10):
        h = _random_hash()
        resp = (
            supa.table("lead_propostas")
            .select("id")
            .eq("public_hash", h)
            .maybe_single()
            .execute()
        )

        data = _get_resp_data(resp)

        if not data:
            # Não achou ninguém com esse hash -> está livre pra usar
            return h

    # fallback bruto se tudo colapsar (chances quase zero)
    return _random_hash(10)


def _load_lead_basic(org_id: str, lead_id: str, supa: Client) -> dict[str, Any]:
    resp = (
        supa.table("leads")
        .select("id, org_id, nome, telefone, email, origem")
        .eq("org_id", org_id)
        .eq("id", lead_id)
        .maybe_single()
        .execute()
    )

    data = _get_resp_data(resp)

    if not data:
        raise ValueError("Lead não encontrado ou de outra organização.")

    return data


def create_lead_proposta(
    org_id: str,
    lead_id: str,
    created_by: Optional[str],
    data: CreateLeadProposalInput,
    supa: Client,
) -> LeadProposalRecord:
    """
    Cria uma nova proposta para um lead, monta o payload JSON
    e retorna o registro recém-criado.
    """

    # 1) Garante que o lead existe e pertence à org
    lead_row = _load_lead_basic(org_id, lead_id, supa)

    cliente = ProposalClientInfo(
        lead_id=lead_row["id"],
        nome=lead_row.get("nome"),
        telefone=lead_row.get("telefone"),
        email=lead_row.get("email"),
        origem=lead_row.get("origem"),
    )

    # 2) Monta cenários a partir do input
    cenarios: list[ProposalScenario] = []
    for c in data.cenarios:
        cenarios.append(
            ProposalScenario(
                id=c.id,
                titulo=c.titulo,
                produto=c.produto,
                administradora=c.administradora,
                valor_carta=c.valor_carta,
                prazo_meses=c.prazo_meses,
                com_redutor=c.com_redutor,
                parcela_cheia=c.parcela_cheia,
                parcela_reduzida=c.parcela_reduzida,
                taxa_admin_anual=c.taxa_admin_anual,
                observacoes=c.observacoes,
            )
        )

    # 3) Meta / contexto
    meta: Optional[ProposalMeta] = data.meta
    if meta is None:
        meta = ProposalMeta(
            campanha=data.campanha,
            comentario_consultor=None,
            validade_dias=7,
        )

    payload = LeadProposalPayload(
        cliente=cliente,
        propostas=cenarios,
        meta=meta,
        extras={
            "cliente_overrides": data.cliente_overrides or {},
        },
    )

    # 4) Gera hash público
    public_hash = _generate_unique_public_hash(supa)

    # Se vier string vazia, trata como None
    if created_by is not None and not str(created_by).strip():
        created_by = None

        # 5) Insere no Supabase
    insert_payload = {
        "org_id": org_id,
        "lead_id": lead_id,
        "titulo": data.titulo,
        "campanha": data.campanha,
        "status": data.status,
        "public_hash": public_hash,
        "payload": payload.dict(),
        "created_by": created_by,  # pode ser None -> vira NULL no Postgres
    }

    resp = supa.table("lead_propostas").insert(insert_payload).execute()
    print("DEBUG lead_propostas insert resp:", resp)

    rows_raw = _get_resp_data(resp)

    # supabase-py geralmente manda uma lista; mas se vier dict, tratamos também:
    if isinstance(rows_raw, dict):
        rows = [rows_raw]
    else:
        rows = rows_raw or []

    if not rows:
        raise RuntimeError(f"Falha ao criar proposta: {getattr(resp, 'error', None)}")

    row = rows[0]

    payload_dict = _normalize_payload(row["payload"])

    return LeadProposalRecord(
        id=row["id"],
        org_id=row["org_id"],
        lead_id=row["lead_id"],
        titulo=row.get("titulo"),
        campanha=row.get("campanha"),
        status=row.get("status"),
        public_hash=row.get("public_hash"),
        payload=LeadProposalPayload(**payload_dict),
        pdf_url=row.get("pdf_url"),
        created_at=row.get("created_at"),
        created_by=row.get("created_by"),
        updated_at=row.get("updated_at"),
    )


def list_lead_propostas(
    org_id: str,
    lead_id: str,
    supa: Client,
) -> list[LeadProposalRecord]:
    """
    Lista propostas já criadas para um lead (pra mostrar na timeline do lead).
    """
    resp = (
        supa.table("lead_propostas")
        .select("*")
        .eq("org_id", org_id)
        .eq("lead_id", lead_id)
        .order("created_at", desc=True)
        .execute()
    )

    rows_raw = _get_resp_data(resp) or []
    if isinstance(rows_raw, dict):
        rows: List[dict[str, Any]] = [rows_raw]
    else:
        rows = rows_raw

    out: list[LeadProposalRecord] = []

    for r in rows:
        try:
            payload_dict = _normalize_payload(r["payload"])
            out.append(
                LeadProposalRecord(
                    id=r["id"],
                    org_id=r["org_id"],
                    lead_id=r["lead_id"],
                    titulo=r.get("titulo"),
                    campanha=r.get("campanha"),
                    status=r.get("status"),
                    public_hash=r.get("public_hash"),
                    payload=LeadProposalPayload(**payload_dict),
                    pdf_url=r.get("pdf_url"),
                    created_at=r.get("created_at"),
                    created_by=r.get("created_by"),
                    updated_at=r.get("updated_at"),
                )
            )
        except Exception as e:
            print("WARN: falha ao parsear proposta, ignorando linha:", repr(e))
            continue
    return out


def get_proposta_by_public_hash(
    public_hash: str,
    supa: Client,
) -> Optional[LeadProposalRecord]:
    """
    Busca a proposta que o cliente vai ver (página pública).
    Não precisa de org_id, porque o hash é randômico + único.
    """
    resp = (
        supa.table("lead_propostas")
        .select("*")
        .eq("public_hash", public_hash)
        .maybe_single()
        .execute()
    )
    row = _get_resp_data(resp)
    if not row:
        return None

    payload_dict = _normalize_payload(row["payload"])

    return LeadProposalRecord(
        id=row["id"],
        org_id=row["org_id"],
        lead_id=row["lead_id"],
        titulo=row.get("titulo"),
        campanha=row.get("campanha"),
        status=row.get("status"),
        public_hash=row.get("public_hash"),
        payload=LeadProposalPayload(**payload_dict),
        pdf_url=row.get("pdf_url"),
        created_at=row.get("created_at"),
        created_by=row.get("created_by"),
        updated_at=row.get("updated_at"),
    )
