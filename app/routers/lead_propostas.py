# app/routers/lead_propostas.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.propostas import CreateLeadProposalInput, LeadProposalRecord
from app.services.lead_propostas_service import (
    create_lead_proposta,
    list_lead_propostas,
    get_proposta_by_public_hash,
)

router = APIRouter(prefix="/lead-propostas", tags=["lead-propostas"])


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
        traceback.print_exc()  # <<< isso imprime o stack trace completo no console

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
