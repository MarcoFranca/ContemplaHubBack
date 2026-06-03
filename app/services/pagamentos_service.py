from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from fastapi import HTTPException
from supabase import Client

from app.schemas.financeiro import PagamentoUpsertIn
from app.services.comissao_competencia_service import processar_pagamento_para_comissao


def _safe_rows(resp: Any) -> List[Dict[str, Any]]:
    return getattr(resp, "data", None) or []


def _safe_one(resp: Any) -> Dict[str, Any] | None:
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_contract_or_404(supa: Client, org_id: str, contrato_id: str) -> Dict[str, Any]:
    resp = (
        supa.table("contratos")
        .select(
            """
            id,
            org_id,
            numero,
            status,
            cota_id,
            cotas (
                id,
                status,
                numero_cota,
                grupo_codigo,
                valor_carta,
                administradora_id,
                administradoras ( id, nome ),
                lead_id,
                leads ( id, nome )
            )
            """
        )
        .eq("org_id", org_id)
        .eq("id", contrato_id)
        .limit(1)
        .execute()
    )
    rows = _safe_rows(resp)
    if not rows:
        raise HTTPException(404, "Contrato não encontrado")
    return rows[0]


def _get_pagamento_or_404(supa: Client, org_id: str, pagamento_id: str) -> Dict[str, Any]:
    resp = (
        supa.table("pagamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("id", pagamento_id)
        .limit(1)
        .execute()
    )
    rows = _safe_rows(resp)
    if not rows:
        raise HTTPException(404, "Pagamento não encontrado")
    return rows[0]


def _normalize_pagamento_payload(
    *,
    body: PagamentoUpsertIn,
    org_id: str,
) -> Dict[str, Any]:
    pago_em = body.pago_em
    if body.status == "pago" and pago_em is None:
        pago_em = datetime.now(timezone.utc)
    if body.status != "pago":
        pago_em = None

    payload = {
        "org_id": org_id,
        "contrato_id": body.contrato_id,
        "tipo": body.tipo,
        "competencia": body.competencia.isoformat(),
        "valor": str(Decimal(body.valor)),
        "pago_em": pago_em.isoformat() if pago_em else None,
        "status": body.status,
        "vencimento": body.vencimento.isoformat() if body.vencimento else None,
        "referencia": body.referencia or body.competencia.strftime("%Y-%m"),
        "origem": body.origem,
        "observacoes": body.observacoes,
        "payload": {
            "source_module": "financeiro_operacional",
        },
    }
    return payload


def _enrich_pagamento_rows(
    supa: Client,
    org_id: str,
    pagamentos: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not pagamentos:
        return []

    contrato_ids = sorted({str(row["contrato_id"]) for row in pagamentos if row.get("contrato_id")})
    contratos_map: Dict[str, Dict[str, Any]] = {}
    if contrato_ids:
        resp = (
            supa.table("contratos")
            .select(
                """
                id,
                numero,
                cota_id,
                cotas (
                    id,
                    numero_cota,
                    grupo_codigo,
                    valor_carta,
                    administradora_id,
                    administradoras ( id, nome ),
                    lead_id,
                    leads ( id, nome )
                )
                """
            )
            .eq("org_id", org_id)
            .in_("id", contrato_ids)
            .execute()
        )
        contratos_map = {row["id"]: row for row in _safe_rows(resp)}

    enriched: List[Dict[str, Any]] = []
    for row in pagamentos:
        contrato = contratos_map.get(str(row.get("contrato_id")))
        cota = (contrato or {}).get("cotas") or {}
        lead = (cota or {}).get("leads") or {}
        enriched.append(
            {
                **row,
                "cota_id": (contrato or {}).get("cota_id"),
                "contrato_numero": (contrato or {}).get("numero"),
                "numero_cota": cota.get("numero_cota"),
                "grupo_codigo": cota.get("grupo_codigo"),
                "cliente_nome": lead.get("nome"),
            }
        )
    return enriched


def create_pagamento(
    supa: Client,
    *,
    org_id: str,
    actor_id: str,
    body: PagamentoUpsertIn,
) -> Dict[str, Any]:
    _get_contract_or_404(supa, org_id, body.contrato_id)
    payload = _normalize_pagamento_payload(body=body, org_id=org_id)
    inserted = (
        supa.table("pagamentos")
        .insert(payload)
        .execute()
    )
    pagamento = _safe_one(inserted)
    if not pagamento:
        raise HTTPException(500, "Erro ao criar pagamento")

    processamento = processar_pagamento_para_comissao(
        supa,
        org_id=org_id,
        pagamento_id=pagamento["id"],
        actor_id=actor_id,
    )

    enriched = _enrich_pagamento_rows(supa, org_id, [pagamento])
    return {
        "ok": True,
        "item": enriched[0] if enriched else pagamento,
        "processamento": processamento,
    }


def update_pagamento(
    supa: Client,
    *,
    org_id: str,
    actor_id: str,
    pagamento_id: str,
    body: PagamentoUpsertIn,
) -> Dict[str, Any]:
    current = _get_pagamento_or_404(supa, org_id, pagamento_id)
    _get_contract_or_404(supa, org_id, body.contrato_id)
    payload = _normalize_pagamento_payload(body=body, org_id=org_id)
    payload["payload"] = {
        **(current.get("payload") or {}),
        "source_module": "financeiro_operacional",
        "updated_by_financeiro": actor_id,
        "updated_at_financeiro": _now_iso(),
    }

    updated = (
        supa.table("pagamentos")
        .update(payload)
        .eq("org_id", org_id)
        .eq("id", pagamento_id)
        .execute()
    )
    pagamento = _safe_one(updated)
    if not pagamento:
        raise HTTPException(500, "Erro ao atualizar pagamento")

    processamento = processar_pagamento_para_comissao(
        supa,
        org_id=org_id,
        pagamento_id=pagamento_id,
        actor_id=actor_id,
    )
    enriched = _enrich_pagamento_rows(supa, org_id, [pagamento])
    return {
        "ok": True,
        "item": enriched[0] if enriched else pagamento,
        "processamento": processamento,
    }


def list_pagamentos_by_contrato(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
) -> Dict[str, Any]:
    _get_contract_or_404(supa, org_id, contrato_id)
    resp = (
        supa.table("pagamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .order("competencia")
        .order("created_at")
        .execute()
    )
    items = _enrich_pagamento_rows(supa, org_id, _safe_rows(resp))
    return {"ok": True, "items": items, "total": len(items)}


def list_pagamentos_by_cota(
    supa: Client,
    *,
    org_id: str,
    cota_id: str,
) -> Dict[str, Any]:
    contratos_resp = (
        supa.table("contratos")
        .select("id")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .execute()
    )
    contratos = _safe_rows(contratos_resp)
    contrato_ids = [row["id"] for row in contratos]
    if not contrato_ids:
        return {"ok": True, "items": [], "total": 0}

    resp = (
        supa.table("pagamentos")
        .select("*")
        .eq("org_id", org_id)
        .in_("contrato_id", contrato_ids)
        .order("competencia")
        .order("created_at")
        .execute()
    )
    items = _enrich_pagamento_rows(supa, org_id, _safe_rows(resp))
    return {"ok": True, "items": items, "total": len(items)}


def list_financeiro_contrato_options(
    supa: Client,
    *,
    org_id: str,
) -> Dict[str, Any]:
    resp = (
        supa.table("contratos")
        .select(
            """
            id,
            numero,
            status,
            cota_id,
            cotas (
                id,
                status,
                numero_cota,
                grupo_codigo,
                valor_carta,
                administradora_id,
                administradoras ( id, nome ),
                lead_id,
                leads ( id, nome )
            )
            """
        )
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
    )
    contratos = _safe_rows(resp)

    config_resp = (
        supa.table("cota_comissao_config")
        .select("cota_id, percentual_total, modo")
        .eq("org_id", org_id)
        .eq("ativo", True)
        .execute()
    )
    config_by_cota = {
        row["cota_id"]: row
        for row in _safe_rows(config_resp)
        if row.get("cota_id")
    }

    parceiros_resp = (
        supa.table("cota_comissao_parceiros")
        .select("cota_id, parceiro_id, percentual_parceiro, ativo, parceiros_corretores ( id, nome, ativo )")
        .eq("org_id", org_id)
        .eq("ativo", True)
        .execute()
    )
    parceiros_by_cota: Dict[str, Dict[str, Any]] = {}
    for row in _safe_rows(parceiros_resp):
        cota_id = row.get("cota_id")
        parceiro = row.get("parceiros_corretores") or {}
        if not cota_id or not parceiro or not parceiro.get("ativo", True):
            continue
        parceiros_by_cota.setdefault(cota_id, row)

    items = []
    for row in contratos:
        cota = row.get("cotas") or {}
        lead = cota.get("leads") or {}
        administradora = cota.get("administradoras") or {}
        config = config_by_cota.get(row.get("cota_id"))
        parceiro = parceiros_by_cota.get(row.get("cota_id"))
        items.append(
            {
                "contrato_id": row["id"],
                "contrato_numero": row.get("numero"),
                "contrato_status": row.get("status"),
                "cota_status": cota.get("status"),
                "cota_id": row.get("cota_id"),
                "numero_cota": cota.get("numero_cota"),
                "grupo_codigo": cota.get("grupo_codigo"),
                "valor_carta": cota.get("valor_carta"),
                "cliente_nome": lead.get("nome"),
                "administradora_nome": administradora.get("nome"),
                "possui_comissao_ativa": bool(config),
                "percentual_comissao": (config or {}).get("percentual_total"),
                "modo_comissao": (config or {}).get("modo"),
                "parceiro_vinculado": bool(parceiro),
                "parceiro_nome": ((parceiro or {}).get("parceiros_corretores") or {}).get("nome"),
                "parceiro_percentual": (parceiro or {}).get("percentual_parceiro"),
            }
        )

    return {"ok": True, "items": items}


def update_contrato_numero(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
    actor_id: str,
    numero_contrato: str,
) -> Dict[str, Any]:
    contrato = _get_contract_or_404(supa, org_id, contrato_id)
    numero = (numero_contrato or "").strip()
    if not numero:
        raise HTTPException(400, "Numero do contrato e obrigatorio")

    duplicate_resp = (
        supa.table("contratos")
        .select("id")
        .eq("org_id", org_id)
        .eq("numero", numero)
        .neq("id", contrato_id)
        .limit(1)
        .execute()
    )
    if _safe_rows(duplicate_resp):
        raise HTTPException(409, "Ja existe outro contrato com esse numero nesta organizacao")

    payload = {
        "numero": numero,
    }

    updated_resp = (
        supa.table("contratos")
        .update(payload)
        .eq("org_id", org_id)
        .eq("id", contrato["id"])
        .execute()
    )
    updated = _safe_one(updated_resp)
    if not updated:
        raise HTTPException(500, "Erro ao atualizar numero do contrato")

    return {
        "ok": True,
        "item": {
            "contrato_id": updated["id"],
            "contrato_numero": updated.get("numero"),
        },
    }
