from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from supabase import Client

from app.core.config import settings
from app.security.auth import AuthContext


def _safe_data(resp: Any) -> Any:
    return getattr(resp, "data", None)


def _to_map(rows: List[dict], key: str) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    for row in rows:
        value = row.get(key)
        if value is not None:
            result[value] = row
    return result


def _mask_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    parts = [p for p in name.strip().split(" ") if p]
    if not parts:
        return None
    if len(parts) == 1:
        base = parts[0]
        if len(base) <= 2:
            return base[0] + "*"
        return base[:2] + "*" * max(1, len(base) - 2)
    first = parts[0]
    last = parts[-1]
    return f"{first} {last[0]}."


def _mask_email(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        local_masked = local[0] + "*"
    else:
        local_masked = local[:2] + "*" * max(1, len(local) - 2)
    return f"{local_masked}@{domain}"


def _mask_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def _paginate(items: List[dict], page: int, page_size: int) -> Tuple[List[dict], dict]:
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    paged = items[start:end]
    meta = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if page_size else 1,
        "has_next": end < total,
        "has_prev": start > 0,
    }
    return paged, meta


def _sort_items(items: List[dict], sort_by: str, sort_order: str, extractor) -> List[dict]:
    reverse = sort_order == "desc"
    return sorted(items, key=lambda item: extractor(item, sort_by), reverse=reverse)


def insert_audit_log(
    supa: Client,
    *,
    org_id: str,
    actor_id: str,
    entity: str,
    entity_id: Optional[str],
    action: str,
    diff: Optional[dict] = None,
) -> None:
    try:
        supa.table("audit_logs").insert(
            {
                "org_id": org_id,
                "actor_id": actor_id,
                "entity": entity,
                "entity_id": entity_id,
                "action": action,
                "diff": diff or {},
            }
        ).execute()
    except Exception:
        pass


def ensure_partner_ctx(ctx: AuthContext) -> None:
    if not ctx.is_partner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso permitido apenas para parceiros",
        )
    if not ctx.parceiro_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Parceiro sem vínculo válido",
        )


def _serialize_cliente_for_partner(lead: Optional[dict], can_view_client_data: bool) -> Optional[dict]:
    if not lead:
        return None

    if can_view_client_data:
        return {
            "id": lead.get("id"),
            "nome": lead.get("nome"),
            "telefone": lead.get("telefone"),
            "email": lead.get("email"),
            "etapa": lead.get("etapa"),
            "owner_id": lead.get("owner_id"),
            "created_at": lead.get("created_at"),
            "cep": lead.get("cep"),
            "logradouro": lead.get("logradouro"),
            "numero": lead.get("numero"),
            "complemento": lead.get("complemento"),
            "bairro": lead.get("bairro"),
            "cidade": lead.get("cidade"),
            "estado": lead.get("estado"),
            "latitude": lead.get("latitude"),
            "longitude": lead.get("longitude"),
            "address_updated_at": lead.get("address_updated_at"),
            "masked": False,
        }

    return {
        "id": lead.get("id"),
        "nome": _mask_name(lead.get("nome")),
        "telefone": _mask_phone(lead.get("telefone")),
        "email": _mask_email(lead.get("email")),
        "etapa": lead.get("etapa"),
        "owner_id": None,
        "created_at": lead.get("created_at"),
        "masked": True,
    }


def get_partner_user_me(
    supa: Client,
    *,
    ctx: AuthContext,
) -> dict:
    ensure_partner_ctx(ctx)

    resp = (
        supa.table("partner_users")
        .select(
            """
            id,
            auth_user_id,
            org_id,
            parceiro_id,
            email,
            nome,
            telefone,
            ativo,
            can_view_client_data,
            can_view_contracts,
            can_view_commissions,
            invite_sent_at,
            access_enabled_at,
            disabled_at,
            disabled_reason,
            last_login_at,
            created_at,
            updated_at
            """
        )
        .eq("id", ctx.partner_user_id)
        .eq("org_id", ctx.org_id)
        .maybe_single()
        .execute()
    )
    item = _safe_data(resp)
    if not item:
        raise HTTPException(404, "Acesso do parceiro não encontrado")

    parceiro_resp = (
        supa.table("parceiros_corretores")
        .select(
            """
            id,
            org_id,
            nome,
            cpf_cnpj,
            telefone,
            email,
            pix_tipo,
            pix_chave,
            ativo,
            observacoes,
            created_at,
            updated_at
            """
        )
        .eq("id", ctx.parceiro_id)
        .eq("org_id", ctx.org_id)
        .maybe_single()
        .execute()
    )
    parceiro = _safe_data(parceiro_resp)

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="partner_portal",
        entity_id=ctx.partner_user_id,
        action="partner_me_viewed",
        diff={},
    )

    return {
        "ok": True,
        "me": item,
        "parceiro": parceiro,
    }


def _fetch_partner_contract_links(
    supa: Client,
    *,
    org_id: str,
    parceiro_id: str,
) -> List[dict]:
    resp = (
        supa.table("contrato_parceiros")
        .select("id, org_id, contrato_id, parceiro_id, origem, principal, observacoes, created_at, updated_at")
        .eq("org_id", org_id)
        .eq("parceiro_id", parceiro_id)
        .order("created_at", desc=True)
        .execute()
    )
    return _safe_data(resp) or []


def _fetch_contracts_by_ids(
    supa: Client,
    *,
    org_id: str,
    contract_ids: List[str],
    status: Optional[str] = None,
) -> List[dict]:
    if not contract_ids:
        return []

    query = (
        supa.table("contratos")
        .select(
            """
            id,
            org_id,
            deal_id,
            cota_id,
            numero,
            data_assinatura,
            status,
            pdf_path,
            pdf_filename,
            pdf_mime_type,
            pdf_size_bytes,
            pdf_uploaded_at,
            pdf_uploaded_by,
            pdf_uploaded_actor_type,
            pdf_version,
            pdf_status,
            created_at,
            data_pagamento,
            data_alocacao,
            data_contemplacao
            """
        )
        .eq("org_id", org_id)
        .in_("id", contract_ids)
    )

    if status:
        query = query.eq("status", status)

    resp = query.execute()
    return _safe_data(resp) or []


def _fetch_cotas_by_ids(
    supa: Client,
    *,
    org_id: str,
    cota_ids: List[str],
) -> List[dict]:
    if not cota_ids:
        return []

    resp = (
        supa.table("cotas")
        .select(
            """
            id,
            org_id,
            lead_id,
            administradora_id,
            valor_carta,
            produto,
            data_adesao,
            observacoes,
            created_at,
            numero_cota,
            grupo_codigo,
            valor_parcela,
            prazo,
            status
            """
        )
        .eq("org_id", org_id)
        .in_("id", cota_ids)
        .execute()
    )
    return _safe_data(resp) or []


def _fetch_leads_by_ids(
    supa: Client,
    *,
    org_id: str,
    lead_ids: List[str],
) -> List[dict]:
    if not lead_ids:
        return []

    resp = (
        supa.table("leads")
        .select(
            "id, org_id, nome, telefone, email, etapa, owner_id, created_at, "
            "cep, logradouro, numero, complemento, bairro, cidade, estado, "
            "latitude, longitude, address_updated_at"
        )
        .eq("org_id", org_id)
        .in_("id", lead_ids)
        .execute()
    )
    return _safe_data(resp) or []


def _fetch_partner_commission_summary_by_contract(
    supa: Client,
    *,
    org_id: str,
    parceiro_id: str,
    contract_ids: List[str],
) -> Dict[str, dict]:
    if not contract_ids:
        return {}

    resp = (
        supa.table("comissao_lancamentos")
        .select(
            """
            contrato_id,
            valor_bruto,
            valor_liquido,
            status,
            repasse_status
            """
        )
        .eq("org_id", org_id)
        .eq("parceiro_id", parceiro_id)
        .in_("contrato_id", contract_ids)
        .execute()
    )
    rows = _safe_data(resp) or []

    summary: Dict[str, dict] = {}
    for row in rows:
        contrato_id = row["contrato_id"]
        item = summary.setdefault(
            contrato_id,
            {
                "total_lancamentos": 0,
                "valor_bruto_total": 0.0,
                "valor_liquido_total": 0.0,
                "pendentes": 0,
                "pagos": 0,
                "repasses_pendentes": 0,
                "repasse_pago": 0,
            },
        )
        item["total_lancamentos"] += 1
        item["valor_bruto_total"] += float(row.get("valor_bruto") or 0)
        item["valor_liquido_total"] += float(row.get("valor_liquido") or 0)

        if row.get("status") == "pago":
            item["pagos"] += 1
        else:
            item["pendentes"] += 1

        if row.get("repasse_status") == "pendente":
            item["repasses_pendentes"] += 1
        elif row.get("repasse_status") == "pago":
            item["repasse_pago"] += 1

    return summary


def _contract_sort_value(item: dict, sort_by: str):
    contrato = item.get("contrato") or {}
    if sort_by == "numero":
        return str(contrato.get("numero") or "")
    if sort_by == "data_assinatura":
        return str(contrato.get("data_assinatura") or "")
    if sort_by == "status":
        return str(contrato.get("status") or "")
    return str(contrato.get("created_at") or "")


def list_partner_contracts(
    supa: Client,
    *,
    ctx: AuthContext,
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> dict:
    ensure_partner_ctx(ctx)

    if not ctx.can_view_contracts:
        raise HTTPException(403, "Parceiro sem permissão para visualizar contratos")

    links = _fetch_partner_contract_links(
        supa,
        org_id=ctx.org_id,
        parceiro_id=ctx.parceiro_id,
    )
    contract_ids = [row["contrato_id"] for row in links if row.get("contrato_id")]

    contracts = _fetch_contracts_by_ids(
        supa,
        org_id=ctx.org_id,
        contract_ids=contract_ids,
        status=status,
    )
    links_map = {row["contrato_id"]: row for row in links}

    cota_ids = [row["cota_id"] for row in contracts if row.get("cota_id")]
    cotas = _fetch_cotas_by_ids(supa, org_id=ctx.org_id, cota_ids=cota_ids)
    cotas_map = _to_map(cotas, "id")

    lead_ids = [row["lead_id"] for row in cotas if row.get("lead_id")]
    leads = _fetch_leads_by_ids(supa, org_id=ctx.org_id, lead_ids=lead_ids)
    leads_map = _to_map(leads, "id")

    commission_summary = _fetch_partner_commission_summary_by_contract(
        supa,
        org_id=ctx.org_id,
        parceiro_id=ctx.parceiro_id,
        contract_ids=[row["id"] for row in contracts],
    )

    items: List[dict] = []
    needle = (q or "").strip().lower()

    for contrato in contracts:
        cota = cotas_map.get(contrato.get("cota_id"))
        lead = leads_map.get(cota.get("lead_id")) if cota else None
        cliente = _serialize_cliente_for_partner(lead, ctx.can_view_client_data)

        item = {
            "link": links_map.get(contrato["id"]),
            "contrato": contrato,
            "cota": cota,
            "cliente": cliente,
            "commission_summary": commission_summary.get(
                contrato["id"],
                {
                    "total_lancamentos": 0,
                    "valor_bruto_total": 0.0,
                    "valor_liquido_total": 0.0,
                    "pendentes": 0,
                    "pagos": 0,
                    "repasses_pendentes": 0,
                    "repasse_pago": 0,
                },
            ),
        }

        if needle:
            haystack = " ".join(
                [
                    str(contrato.get("numero") or ""),
                    str(cota.get("numero_cota") if cota else ""),
                    str(cota.get("grupo_codigo") if cota else ""),
                    str((lead or {}).get("nome") or ""),
                ]
            ).lower()
            if needle not in haystack:
                continue

        items.append(item)

    items = _sort_items(items, sort_by, sort_order, _contract_sort_value)
    paged_items, meta = _paginate(items, page, page_size)

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="partner_portal",
        entity_id=ctx.partner_user_id,
        action="partner_contracts_list_viewed",
        diff={
            "status": status,
            "q": q,
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "result_count": len(paged_items),
            "total_filtered": len(items),
        },
    )

    return {
        "ok": True,
        "items": paged_items,
        "meta": meta,
    }


def get_partner_contract_detail(
    supa: Client,
    *,
    ctx: AuthContext,
    contract_id: str,
) -> dict:
    ensure_partner_ctx(ctx)

    if not ctx.can_view_contracts:
        raise HTTPException(403, "Parceiro sem permissão para visualizar contratos")

    links = _fetch_partner_contract_links(
        supa,
        org_id=ctx.org_id,
        parceiro_id=ctx.parceiro_id,
    )
    allowed_contract_ids = {row["contrato_id"] for row in links if row.get("contrato_id")}
    if contract_id not in allowed_contract_ids:
        raise HTTPException(403, "Parceiro sem acesso a este contrato")

    contracts = _fetch_contracts_by_ids(
        supa,
        org_id=ctx.org_id,
        contract_ids=[contract_id],
    )
    if not contracts:
        raise HTTPException(404, "Contrato não encontrado")

    contrato = contracts[0]
    cota = None
    lead = None

    if contrato.get("cota_id"):
        cotas = _fetch_cotas_by_ids(supa, org_id=ctx.org_id, cota_ids=[contrato["cota_id"]])
        cota = cotas[0] if cotas else None

    if cota and cota.get("lead_id"):
        leads = _fetch_leads_by_ids(supa, org_id=ctx.org_id, lead_ids=[cota["lead_id"]])
        lead = leads[0] if leads else None

    commissions_resp = (
        supa.table("comissao_lancamentos")
        .select(
            """
            id,
            org_id,
            contrato_id,
            cota_id,
            parceiro_id,
            beneficiario_tipo,
            tipo_evento,
            ordem,
            competencia_prevista,
            competencia_real,
            percentual_base,
            valor_base,
            valor_bruto,
            imposto_pct,
            valor_imposto,
            valor_liquido,
            status,
            pago_em,
            repasse_status,
            repasse_previsto_em,
            repasse_pago_em,
            repasse_observacoes,
            observacoes,
            created_at,
            updated_at
            """
        )
        .eq("org_id", ctx.org_id)
        .eq("contrato_id", contract_id)
        .eq("parceiro_id", ctx.parceiro_id)
        .order("ordem", desc=False)
        .execute()
    )
    commission_items = _safe_data(commissions_resp) or []

    summary = _fetch_partner_commission_summary_by_contract(
        supa,
        org_id=ctx.org_id,
        parceiro_id=ctx.parceiro_id,
        contract_ids=[contract_id],
    ).get(
        contract_id,
        {
            "total_lancamentos": 0,
            "valor_bruto_total": 0.0,
            "valor_liquido_total": 0.0,
            "pendentes": 0,
            "pagos": 0,
            "repasses_pendentes": 0,
            "repasse_pago": 0,
        },
    )

    link = next((row for row in links if row["contrato_id"] == contract_id), None)
    cliente = _serialize_cliente_for_partner(lead, ctx.can_view_client_data)

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="contrato",
        entity_id=contract_id,
        action="partner_contract_detail_viewed",
        diff={},
    )

    return {
        "ok": True,
        "item": {
            "link": link,
            "contrato": contrato,
            "cota": cota,
            "cliente": cliente,
            "commission_summary": summary,
            "commission_items": commission_items if ctx.can_view_commissions else [],
        },
    }


def _commission_sort_value(item: dict, sort_by: str):
    if sort_by == "valor_bruto":
        return float(item.get("valor_bruto") or 0)
    if sort_by == "valor_liquido":
        return float(item.get("valor_liquido") or 0)
    if sort_by == "status":
        return str(item.get("status") or "")
    if sort_by == "repasse_status":
        return str(item.get("repasse_status") or "")
    if sort_by == "created_at":
        return str(item.get("created_at") or "")
    if sort_by == "competencia_real":
        return str(item.get("competencia_real") or "")
    return str(item.get("competencia_prevista") or "")


def list_partner_commissions(
    supa: Client,
    *,
    ctx: AuthContext,
    status: Optional[str] = None,
    repasse_status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "competencia_prevista",
    sort_order: str = "desc",
) -> dict:
    ensure_partner_ctx(ctx)

    if not ctx.can_view_commissions:
        raise HTTPException(403, "Parceiro sem permissão para visualizar comissões")

    query = (
        supa.table("comissao_lancamentos")
        .select(
            """
            id,
            org_id,
            contrato_id,
            cota_id,
            parceiro_id,
            beneficiario_tipo,
            tipo_evento,
            ordem,
            competencia_prevista,
            competencia_real,
            percentual_base,
            valor_base,
            valor_bruto,
            imposto_pct,
            valor_imposto,
            valor_liquido,
            status,
            pago_em,
            repasse_status,
            repasse_previsto_em,
            repasse_pago_em,
            repasse_observacoes,
            observacoes,
            created_at,
            updated_at
            """
        )
        .eq("org_id", ctx.org_id)
        .eq("parceiro_id", ctx.parceiro_id)
        .eq("beneficiario_tipo", "parceiro")
    )

    if status:
        query = query.eq("status", status)

    if repasse_status:
        query = query.eq("repasse_status", repasse_status)

    resp = query.execute()
    items = _safe_data(resp) or []

    needle = (q or "").strip().lower()
    if needle:
        filtered: List[dict] = []
        for row in items:
            haystack = " ".join(
                [
                    str(row.get("contrato_id") or ""),
                    str(row.get("cota_id") or ""),
                    str(row.get("tipo_evento") or ""),
                    str(row.get("status") or ""),
                    str(row.get("repasse_status") or ""),
                    str(row.get("observacoes") or ""),
                    str(row.get("repasse_observacoes") or ""),
                ]
            ).lower()

            if needle in haystack:
                filtered.append(row)

        items = filtered

    items = _sort_items(items, sort_by, sort_order, _commission_sort_value)
    paged_items, meta = _paginate(items, page, page_size)

    total_bruto = 0.0
    total_liquido = 0.0
    pagos = 0
    pendentes = 0
    repasse_pendente = 0
    repasse_pago = 0

    for row in items:
        total_bruto += float(row.get("valor_bruto") or 0)
        total_liquido += float(row.get("valor_liquido") or 0)

        if row.get("status") == "pago":
            pagos += 1
        else:
            pendentes += 1

        if row.get("repasse_status") == "pendente":
            repasse_pendente += 1
        elif row.get("repasse_status") == "pago":
            repasse_pago += 1

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="partner_portal",
        entity_id=ctx.partner_user_id,
        action="partner_commissions_list_viewed",
        diff={
            "status": status,
            "repasse_status": repasse_status,
            "q": q,
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "result_count": len(paged_items),
            "total_filtered": len(items),
        },
    )

    return {
        "ok": True,
        "items": paged_items,
        "meta": meta,
        "resumo": {
            "total_lancamentos": len(items),
            "valor_bruto_total": total_bruto,
            "valor_liquido_total": total_liquido,
            "pagos": pagos,
            "pendentes": pendentes,
            "repasse_pendente": repasse_pendente,
            "repasse_pago": repasse_pago,
        },
    }


def create_partner_contract_signed_url(
    supa: Client,
    *,
    ctx: AuthContext,
    contract_id: str,
    expires_in: Optional[int] = None,
) -> dict:
    ensure_partner_ctx(ctx)

    if not ctx.can_view_contracts:
        raise HTTPException(403, "Parceiro sem permissão para visualizar contratos")

    links = _fetch_partner_contract_links(
        supa,
        org_id=ctx.org_id,
        parceiro_id=ctx.parceiro_id,
    )
    allowed_contract_ids = {row["contrato_id"] for row in links if row.get("contrato_id")}
    if contract_id not in allowed_contract_ids:
        raise HTTPException(403, "Parceiro sem acesso a este contrato")

    contract_resp = (
        supa.table("contratos")
        .select("id, org_id, pdf_path, pdf_filename, pdf_status")
        .eq("org_id", ctx.org_id)
        .eq("id", contract_id)
        .maybe_single()
        .execute()
    )
    contrato = _safe_data(contract_resp)
    if not contrato:
        raise HTTPException(404, "Contrato não encontrado")
    if not contrato.get("pdf_path"):
        raise HTTPException(404, "Contrato sem documento PDF")

    ttl = expires_in or settings.CONTRACTS_SIGNED_URL_EXPIRES_IN

    try:
        signed = supa.storage.from_(settings.CONTRACTS_BUCKET).create_signed_url(
            contrato["pdf_path"],
            ttl,
        )
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar URL assinada: {str(e)}")

    signed_url = signed.get("signedURL") or signed.get("signedUrl") or signed.get("signed_url")
    if not signed_url:
        raise HTTPException(500, "Falha ao gerar URL assinada")

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="contrato",
        entity_id=contract_id,
        action="partner_contract_signed_url_created",
        diff={"expires_in": ttl},
    )

    return {
        "ok": True,
        "contract_id": contract_id,
        "signed_url": signed_url,
        "expires_in": ttl,
        "pdf_filename": contrato.get("pdf_filename"),
    }
