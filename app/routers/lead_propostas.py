from __future__ import annotations
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.propostas import CreateLeadProposalInput, LeadProposalRecord
from app.services.email_service import send_system_email
from app.services.lead_propostas_service import (
    create_lead_proposta,
    list_lead_propostas,
    get_proposta_by_public_hash,
    delete_proposta,
    update_proposta_status,
    inativar_proposta,
    get_proposta_by_id,
)

router = APIRouter(prefix="/lead-propostas", tags=["lead-propostas"])


class AcceptPropostaPayload(BaseModel):
    source: str | None = None
    ip: str | None = None
    user_agent: str | None = None


def notify_email_proposta_aprovada(
    supa: Client,
    proposta: LeadProposalRecord,
    payload: AcceptPropostaPayload,
) -> None:
    """
    Notifica a organização (org.email_from) que o cliente marcou a proposta como APROVADA.
    """

    user_email: str | None = None
    user_name: str | None = None

    # 1) Buscar e-mail da organização (orgs.email_from)
    try:
        resp = (
            supa.table("orgs")
            .select("email_from, nome")
            .eq("id", proposta.org_id)
            .maybe_single()
            .execute()
        )
        data = getattr(resp, "data", None)
        if data:
            user_email = data.get("email_from")
            user_name = data.get("nome")
    except Exception as e:
        print("WARN: erro ao buscar e-mail da org:", repr(e))

    if not user_email:
        print("WARN: nenhum e-mail de destino para notificar proposta aprovada (org.email_from vazio)")
        return

    # 2) Montar assunto e corpo
    cliente_nome = (
        proposta.payload.cliente.nome
        if proposta.payload and proposta.payload.cliente
        else None
    )

    subject = f"Proposta aprovada pelo cliente – {proposta.titulo or 'Sem título'}"

    body_lines = [
        f"Olá{f', {user_name}' if user_name else ''}!",
        "",
        "Uma proposta foi marcada como APROVADA pelo cliente na página pública.",
        "",
        f"Título da proposta: {proposta.titulo or 'Sem título'}",
        f"Cliente: {cliente_nome or '—'}",
        f"Lead ID: {proposta.lead_id}",
        f"Proposta ID: {proposta.id}",
        "",
        f"Origem da ação: {payload.source or 'public_proposal_page'}",
        f"IP do cliente: {payload.ip or '—'}",
        f"User-Agent do cliente: {payload.user_agent or '—'}",
        "",
        "Acesse o ContemplaHub para seguir com a contratação:",
        "- Conferir documentos",
        "- Orientar o cliente sobre os próximos passos",
        "- Atualizar o status no funil, se necessário.",
        "",
        "Abraços,",
        "ContemplaHub / Autentika",
    ]

    body = "\n".join(body_lines)

    # 3) Disparar e-mail via Resend
    send_system_email(to=user_email, subject=subject, text_body=body)


class UpdateStatusBody(BaseModel):
    status: Literal["rascunho", "enviada", "aprovada", "recusada", "inativa"]


@router.patch("/{proposta_id}/status", response_model=LeadProposalRecord)
def api_update_proposta_status(
    proposta_id: str,
    body: UpdateStatusBody,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )
    try:
        return update_proposta_status(
            org_id=x_org_id,
            proposta_id=proposta_id,
            novo_status=body.status,
            supa=supa,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print("ERRO ao atualizar status proposta:", repr(e))
        raise HTTPException(500, "Erro ao atualizar status da proposta.")


@router.patch("/{proposta_id}/inativar", response_model=LeadProposalRecord)
def api_inativar_proposta(
    proposta_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )
    try:
        return inativar_proposta(
            org_id=x_org_id,
            proposta_id=proposta_id,
            supa=supa,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print("ERRO ao inativar proposta:", repr(e))
        raise HTTPException(500, "Erro ao inativar proposta.")


@router.delete("/{proposta_id}", status_code=204)
def api_delete_proposta(
    proposta_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )
    try:
        delete_proposta(
            org_id=x_org_id,
            proposta_id=proposta_id,
            supa=supa,
        )
        return
    except Exception as e:
        print("ERRO ao deletar proposta:", repr(e))
        raise HTTPException(500, "Erro ao deletar proposta.")


@router.get("/{proposta_id}", response_model=LeadProposalRecord)
def api_get_proposta_by_id(
    proposta_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    """
    Retorna uma proposta interna pelo ID (usada na página interna do lead).
    """
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )

    try:
        rec = get_proposta_by_id(
            org_id=x_org_id,
            proposta_id=proposta_id,
            supa=supa,
        )
    except Exception as e:
        print("ERRO ao buscar proposta por id:", repr(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao buscar proposta.",
        )

    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposta não encontrada.",
        )

    return rec


@router.get("/lead/{lead_id}", response_model=list[LeadProposalRecord])
def api_list_lead_propostas(
    lead_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )

    return list_lead_propostas(x_org_id, lead_id, supa)


@router.post("/lead/{lead_id}", response_model=LeadProposalRecord)
def api_create_lead_proposta(
    lead_id: str,
    body: CreateLeadProposalInput,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )

    try:
        return create_lead_proposta(
            org_id=x_org_id,
            lead_id=lead_id,
            created_by=x_user_id,
            data=body,
            supa=supa,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        import traceback

        print("ERRO ao criar proposta:", repr(e))
        traceback.print_exc()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno ao criar proposta: {repr(e)}",
        )


@router.get("/p/{public_hash}", response_model=LeadProposalRecord)
def api_get_public_proposta(
    public_hash: str,
    supa: Client = Depends(get_supabase_admin),
):
    rec = get_proposta_by_public_hash(public_hash, supa)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposta não encontrada.",
        )
    return rec


class AcceptPropostaPayload(BaseModel):
    source: str | None = None
    ip: str | None = None
    user_agent: str | None = None


@router.post("/p/{public_hash}/accept")
def api_accept_public_proposta(
    public_hash: str,
    payload: AcceptPropostaPayload,
    supa: Client = Depends(get_supabase_admin),
):
    # 1) localizar proposta pelo hash público
    rec = get_proposta_by_public_hash(public_hash, supa)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposta não encontrada.",
        )

    # 2) marcar como APROVADA
    try:
        updated = update_proposta_status(
            org_id=rec.org_id,
            proposta_id=rec.id,
            novo_status="aprovada",
            supa=supa,
        )
    except Exception as e:
        print("ERRO ao marcar proposta como aprovada:", repr(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao marcar proposta como aprovada.",
        )

    # 3) notificar consultor / time interno por e-mail (não quebra se falhar)
    try:
        notify_email_proposta_aprovada(
            supa=supa,
            proposta=updated,
            payload=payload,
        )
    except Exception as e:
        print("WARN: falha ao enviar e-mail de proposta aprovada:", repr(e))

    return {"ok": True}

