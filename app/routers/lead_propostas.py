from __future__ import annotations
from typing import Literal
import os

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
FRONTEND_APP_URL = os.getenv("FRONTEND_APP_URL", "https://app.contemplahub.com")

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
    Envia texto + HTML bonitinho.
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

    # 2) Dados básicos
    cliente_nome = (
        proposta.payload.cliente.nome
        if proposta.payload and proposta.payload.cliente
        else None
    )

    subject = f"Proposta aprovada pelo cliente – {proposta.titulo or 'Sem título'}"

    # ---------- TEXTO SIMPLES (fallback) ----------
    text_body_lines = [
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
        f"Abrir proposta: {FRONTEND_APP_URL}/app/leads/{proposta.lead_id}/propostas/{proposta.id}",
        "",
        "Abraços,",
        "ContemplaHub / Autentika",
    ]
    text_body = "\n".join(text_body_lines)

    # ---------- HTML BONITO ----------
    main_scenario = None
    try:
        if proposta.payload and proposta.payload.propostas:
            main_scenario = proposta.payload.propostas[0]
    except Exception:
        pass

    lead_url = f"{FRONTEND_APP_URL}/app/leads/{proposta.lead_id}"
    proposta_url = f"{FRONTEND_APP_URL}/app/leads/{proposta.lead_id}/propostas/{proposta.id}"

    valor_carta_str = ""
    parcela_str = ""

    if main_scenario and main_scenario.valor_carta is not None:
        valor_carta_str = f"R$ {main_scenario.valor_carta:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    parcela_val = (
        main_scenario.parcela_reduzida
        if main_scenario and main_scenario.parcela_reduzida is not None
        else main_scenario.parcela_cheia
        if main_scenario and main_scenario.parcela_cheia is not None
        else None
    )
    if parcela_val is not None:
        parcela_str = f"R$ {parcela_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    html_body = f"""
<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8" />
    <title>{subject}</title>
  </head>
  <body style="margin:0; padding:0; background-color:#020617; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background-color:#020617; padding:24px 0;">
      <tr>
        <td align="center">
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:640px; background-color:#020617; color:#e5e7eb;">
            <!-- Header -->
            <tr>
              <td style="padding:16px 24px 8px 24px; text-align:left;">
                <div style="font-size:11px; letter-spacing:0.18em; text-transform:uppercase; color:#34d399;">
                  ContemplaHub · Autentika
                </div>
                <div style="margin-top:4px; font-size:18px; font-weight:600; color:#f9fafb;">
                  Proposta aprovada pelo cliente
                </div>
              </td>
            </tr>

            <!-- Card principal -->
            <tr>
              <td style="padding:8px 24px 24px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="border-radius:16px; border:1px solid #10b98133; background:linear-gradient(135deg,#020617,#020617,#022c22);">
                  <tr>
                    <td style="padding:20px 20px 16px 20px;">
                      <div style="font-size:14px; font-weight:600; color:#ecfdf5; margin-bottom:4px;">
                        {proposta.titulo or 'Proposta de consórcio'}
                      </div>
                      <div style="font-size:12px; color:#9ca3af;">
                        Cliente: <strong>{cliente_nome or '—'}</strong>
                      </div>
                      <div style="font-size:11px; color:#6b7280; margin-top:4px;">
                        Status atual: <span style="color:#34d399; font-weight:500;">Aprovada pelo cliente</span>
                      </div>
                    </td>
                  </tr>

                  <!-- Linha de resumo -->
                  <tr>
                    <td style="padding:0 20px 16px 20px;">
                      <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
                        <tr>
                          <td style="width:33%; padding-right:8px;">
                            <div style="font-size:11px; text-transform:uppercase; color:#9ca3af; letter-spacing:0.12em;">Valor da carta</div>
                            <div style="margin-top:4px; font-size:14px; font-weight:600; color:#e5e7eb;">
                              {valor_carta_str or '—'}
                            </div>
                          </td>
                          <td style="width:33%; padding-right:8px;">
                            <div style="font-size:11px; text-transform:uppercase; color:#9ca3af; letter-spacing:0.12em;">Parcela estimada</div>
                            <div style="margin-top:4px; font-size:14px; font-weight:600; color:#a7f3d0;">
                              {parcela_str or '—'}
                            </div>
                          </td>
                          <td style="width:34%;">
                            <div style="font-size:11px; text-transform:uppercase; color:#9ca3af; letter-spacing:0.12em;">Lead / Proposta</div>
                            <div style="margin-top:4px; font-size:12px; color:#e5e7eb;">
                              <span style="display:block;">Lead: {proposta.lead_id}</span>
                              <span style="display:block;">Proposta: {proposta.id}</span>
                            </div>
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>

                  <!-- CTA -->
                  <tr>
                    <td style="padding:0 20px 20px 20px;">
                      <p style="margin:0 0 12px 0; font-size:12px; color:#d1d5db;">
                        A proposta foi aprovada pelo cliente na página pública.
                        Acesse o ContemplaHub para seguir com a contratação, conferir documentos
                        e registrar os próximos passos no funil.
                      </p>

                      <table cellpadding="0" cellspacing="0" role="presentation">
                        <tr>
                          <td align="left" style="border-radius:999px; background-color:#10b981;">
                            <a href="{proposta_url}"
                               style="display:inline-block; padding:10px 18px; font-size:13px; font-weight:600; color:#020617; text-decoration:none; border-radius:999px;">
                              Abrir proposta no ContemplaHub
                            </a>
                          </td>
                        </tr>
                      </table>

                      <p style="margin:12px 0 0 0; font-size:11px; color:#6b7280;">
                        Se preferir, você também pode abrir direto pelo lead:
                        <a href="{lead_url}" style="color:#34d399; text-decoration:none;">ver lead no ContemplaHub</a>.
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <!-- Rodapé -->
            <tr>
              <td style="padding:8px 24px 0 24px;">
                <p style="margin:0; font-size:11px; color:#6b7280;">
                  Origem da ação: {payload.source or 'public_proposal_page'} ·
                  IP: {payload.ip or '—'}
                </p>
                <p style="margin:4px 0 0 0; font-size:11px; color:#4b5563;">
                  ContemplaHub · Autentika Consórcios
                </p>
              </td>
            </tr>

          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
    """.strip()

    # 3) Disparar e-mail via Resend (texto + HTML)
    send_system_email(
        to=user_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


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

