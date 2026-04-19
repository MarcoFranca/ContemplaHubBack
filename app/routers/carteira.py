from __future__ import annotations

from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr
from supabase import Client

from app.deps import get_supabase_admin
from app.services.lead_address_service import apply_lead_address_rules
from app.services.kanban_service import move_lead_stage

router = APIRouter(prefix="/carteira", tags=["carteira"])


# ======================================================
# HELPERS
# ======================================================
def ensure_carteira_cliente(
    *,
    supa: Client,
    org_id: str,
    lead_id: str,
    origem_entrada: str,
    observacoes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Garante que o lead exista em carteira_clientes.
    Se já existir, não duplica.
    """
    existing = (
        supa.table("carteira_clientes")
        .select("id, org_id, lead_id, status, origem_entrada")
        .eq("org_id", org_id)
        .eq("lead_id", lead_id)
        .limit(1)
        .execute()
    )

    rows = getattr(existing, "data", None) or []
    if rows:
        return {
            "ok": True,
            "created": False,
            "carteira_cliente": rows[0],
        }

    payload = {
        "org_id": org_id,
        "lead_id": lead_id,
        "status": "ativo",
        "origem_entrada": origem_entrada,
        "observacoes": observacoes,
    }

    created = (
        supa.table("carteira_clientes")
        .insert(payload, returning="representation")
        .execute()
    )

    data = getattr(created, "data", None) or []
    if not data:
        raise HTTPException(500, "Erro ao inserir cliente na carteira")

    return {
        "ok": True,
        "created": True,
        "carteira_cliente": data[0],
    }


def get_lead_or_404(*, supa: Client, lead_id: str) -> Dict[str, Any]:
    resp = (
        supa.table("leads")
        .select(
            "id, org_id, nome, telefone, email, etapa, owner_id, "
            "cep, logradouro, numero, complemento, bairro, cidade, estado, "
            "latitude, longitude, address_updated_at"
        )
        .eq("id", lead_id)
        .single()
        .execute()
    )
    lead = getattr(resp, "data", None)
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    return lead


# ======================================================
# MODELOS
# ======================================================
class CreateCarteiraClienteIn(BaseModel):
    nome: str
    telefone: Optional[str] = None
    email: Optional[EmailStr] = None
    owner_id: Optional[str] = None
    observacoes: Optional[str] = None
    cep: Optional[str] = None
    logradouro: Optional[str] = None
    numero: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class NovaNegociacaoIn(BaseModel):
    stage: str = "negociacao"
    reason: Optional[str] = "Nova negociação iniciada pela carteira"


# ======================================================
# GET /carteira
# Lista clientes da carteira
# ======================================================
@router.get("")
def list_carteira(
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório",
        )

    # Base da carteira = carteira_clientes
    resp = (
        supa.table("carteira_clientes")
        .select("""
            id,
            org_id,
            lead_id,
            status,
            origem_entrada,
            entered_at,
            observacoes,
            leads!inner (
                id,
                nome,
                telefone,
                email,
                etapa,
                cep,
                logradouro,
                numero,
                complemento,
                bairro,
                cidade,
                estado,
                latitude,
                longitude,
                address_updated_at
            )
        """)
        .eq("org_id", x_org_id)
        .order("entered_at", desc=True)
        .execute()
    )

    rows = getattr(resp, "data", None) or []

    # Enriquecer com cota/contrato mais recente por lead
    result: List[Dict[str, Any]] = []

    for row in rows:
        lead_id = row["lead_id"]

        cotas_resp = (
            supa.table("cotas")
            .select("""
                id,
                lead_id,
                administradora_id,
                numero_cota,
                grupo_codigo,
                valor_carta,
                valor_parcela,
                prazo,
                autorizacao_gestao,
                produto
            """)
            .eq("org_id", x_org_id)
            .eq("lead_id", lead_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        cotas = getattr(cotas_resp, "data", None) or []
        cota = cotas[0] if cotas else None

        contrato = None
        administradora = None

        if cota:
            contrato_resp = (
                supa.table("contratos")
                .select("""
                    id,
                    cota_id,
                    numero,
                    status,
                    data_assinatura,
                    data_pagamento,
                    data_alocacao,
                    data_contemplacao
                """)
                .eq("org_id", x_org_id)
                .eq("cota_id", cota["id"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            contratos = getattr(contrato_resp, "data", None) or []
            contrato = contratos[0] if contratos else None

            if cota.get("administradora_id"):
                adm_resp = (
                    supa.table("administradoras")
                    .select("id, nome")
                    .eq("org_id", x_org_id)
                    .eq("id", cota["administradora_id"])
                    .limit(1)
                    .execute()
                )
                adms = getattr(adm_resp, "data", None) or []
                administradora = adms[0] if adms else None

        result.append({
            "carteira_id": row["id"],
            "lead_id": row["lead_id"],
            "status_carteira": row["status"],
            "origem_entrada": row["origem_entrada"],
            "entered_at": row["entered_at"],
            "observacoes": row.get("observacoes"),
            "lead": row.get("leads"),
            "cota": cota,
            "contrato": contrato,
            "administradora": administradora,
        })

    return {
        "ok": True,
        "items": result,
        "total": len(result),
    }


# ======================================================
# POST /carteira/clientes
# Cria lead + coloca na carteira
# ======================================================
@router.post("/clientes")
def create_cliente_direto_na_carteira(
    body: CreateCarteiraClienteIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório",
        )

    lead_payload = apply_lead_address_rules(
        {
            "org_id": x_org_id,
            "nome": body.nome,
            "telefone": body.telefone,
            "email": str(body.email) if body.email else None,
            "owner_id": body.owner_id,
            "etapa": "ativo",
            "cep": body.cep,
            "logradouro": body.logradouro,
            "numero": body.numero,
            "complemento": body.complemento,
            "bairro": body.bairro,
            "cidade": body.cidade,
            "estado": body.estado,
            "latitude": body.latitude,
            "longitude": body.longitude,
        }
    )

    lead_resp = (
        supa.table("leads")
        .insert(lead_payload, returning="representation")
        .execute()
    )

    lead_rows = getattr(lead_resp, "data", None) or []
    if not lead_rows:
        raise HTTPException(500, "Erro ao criar lead para carteira")

    lead = lead_rows[0]

    carteira_result = ensure_carteira_cliente(
        supa=supa,
        org_id=x_org_id,
        lead_id=lead["id"],
        origem_entrada="manual",
        observacoes=body.observacoes or "Cliente criado diretamente na carteira",
    )

    return {
        "ok": True,
        "lead": lead,
        "carteira": carteira_result["carteira_cliente"],
    }


# ======================================================
# POST /carteira/{lead_id}/nova-negociacao
# Cliente volta ao kanban, mas permanece na carteira
# ======================================================
@router.post("/{lead_id}/nova-negociacao")
def abrir_nova_negociacao(
    lead_id: str,
    body: NovaNegociacaoIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(400, "X-Org-Id header é obrigatório")

    lead = get_lead_or_404(supa=supa, lead_id=lead_id)

    if lead["org_id"] != x_org_id:
        raise HTTPException(403, "Lead pertence a outra organização")

    # Garante que esteja na carteira
    carteira_result = ensure_carteira_cliente(
        supa=supa,
        org_id=x_org_id,
        lead_id=lead_id,
        origem_entrada="manual",
        observacoes="Reentrada em negociação pela carteira",
    )

    # Move lead de volta ao fluxo comercial
    result = move_lead_stage(
        org_id=x_org_id,
        lead_id=lead_id,
        new_stage=body.stage,  # ex.: negociacao
        supa=supa,
        reason=body.reason,
    )

    if not result.get("ok"):
        raise HTTPException(500, result.get("message", "Erro ao mover lead para negociação"))

    return {
        "ok": True,
        "lead_id": lead_id,
        "carteira_preservada": True,
        "carteira": carteira_result["carteira_cliente"],
        "kanban": result,
    }
