from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Tuple

from fastapi import HTTPException
from supabase import Client

from app.schemas.financeiro import PagamentoUpsertIn
from app.services.comissao_competencia_service import (
    _resolve_regra_competencia_prevista,
    processar_pagamento_para_comissao,
    reprocessar_comissoes_contrato,
)
from app.services.comissao_service import (
    fetch_config_by_cota,
    fetch_contrato_context,
    fetch_cota_context,
    fetch_regras,
)

MONEY_Q = Decimal("0.01")


def _safe_rows(resp: Any) -> List[Dict[str, Any]]:
    return getattr(resp, "data", None) or []


def _safe_one(resp: Any) -> Dict[str, Any] | None:
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _add_months(d: date, months: int) -> date:
    month_index = (d.month - 1) + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _get_contract_or_404(supa: Client, org_id: str, contrato_id: str) -> Dict[str, Any]:
    resp = (
        supa.table("contratos")
        .select(
            """
            id,
            org_id,
            numero,
            status,
            data_contemplacao,
            cota_id,
            cotas (
                id,
                status,
                numero_cota,
                grupo_codigo,
                valor_carta,
                administradora_id,
                data_adesao,
                assembleia_dia,
                furo_meses,
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

    return {
        "org_id": org_id,
        "contrato_id": body.contrato_id,
        "tipo": body.tipo,
        "competencia": body.competencia.isoformat(),
        "valor": str(_money(Decimal(body.valor))),
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


def _resolve_pagamento_vencimento(competencia: date, cota: Dict[str, Any]) -> date:
    base_day = int(cota.get("assembleia_dia") or 10)
    base_day = min(max(base_day, 1), 28)
    return competencia.replace(day=base_day)


def _find_pagamento_cronograma_existente(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
    competencia: date,
    regra_id: str,
) -> Dict[str, Any] | None:
    resp = (
        supa.table("pagamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .eq("competencia", competencia.isoformat())
        .eq("tipo", "parcela_mensal")
        .eq("origem", "manual")
        .execute()
    )
    rows = _safe_rows(resp)
    for row in rows:
        payload = row.get("payload") or {}
        if (
            payload.get("source_module") == "financeiro_cronograma_comissao"
            and str(payload.get("regra_id")) == str(regra_id)
        ):
            return row
    return None


def _upsert_pagamento_cronograma(
    supa: Client,
    *,
    org_id: str,
    actor_id: str,
    contrato: Dict[str, Any],
    cota: Dict[str, Any],
    regra: Dict[str, Any],
    competencia: date,
    valor: Decimal,
) -> Tuple[Dict[str, Any], str]:
    vencimento = _resolve_pagamento_vencimento(competencia, cota)
    existing = _find_pagamento_cronograma_existente(
        supa,
        org_id=org_id,
        contrato_id=contrato["id"],
        competencia=competencia,
        regra_id=regra["id"],
    )

    source_payload = {
        "source_module": "financeiro_cronograma_comissao",
        "regra_id": regra["id"],
        "ordem": int(regra.get("ordem") or 0),
        "tipo_evento": regra.get("tipo_evento"),
        "actor_id": actor_id,
        "cronograma_confirmado_em": _now_iso(),
    }

    payload = {
        "org_id": org_id,
        "contrato_id": contrato["id"],
        "tipo": "parcela_mensal",
        "competencia": competencia.isoformat(),
        "valor": str(_money(valor)),
        "status": "previsto",
        "vencimento": vencimento.isoformat(),
        "referencia": f"Comissão prevista #{int(regra.get('ordem') or 0)}",
        "origem": "manual",
        "observacoes": "Cronograma previsto da comissão confirmado operacionalmente.",
        "payload": source_payload,
    }

    if existing:
        existing_status = (existing.get("status") or "previsto").lower()
        update_payload = {
            **payload,
            "payload": {
                **(existing.get("payload") or {}),
                **source_payload,
                "updated_at_financeiro": _now_iso(),
                "updated_by_financeiro": actor_id,
            },
        }
        if existing_status == "pago":
            update_payload["status"] = existing.get("status")
            update_payload["pago_em"] = existing.get("pago_em")
        elif existing_status in {"inadimplente", "cancelado"}:
            update_payload["status"] = existing_status
            update_payload["pago_em"] = None
        updated = (
            supa.table("pagamentos")
            .update(update_payload)
            .eq("org_id", org_id)
            .eq("id", existing["id"])
            .execute()
        )
        row = _safe_one(updated) or {**existing, **update_payload}
        return row, "updated"

    payload["pago_em"] = None
    inserted = supa.table("pagamentos").insert(payload).execute()
    row = _safe_one(inserted)
    if not row:
        raise HTTPException(500, "Erro ao criar pagamento previsto do cronograma")
    return row, "created"


def _cancel_stale_pagamentos_cronograma(
    supa: Client,
    *,
    org_id: str,
    actor_id: str,
    contrato_id: str,
    keep_pagamento_ids: List[str],
) -> int:
    resp = (
        supa.table("pagamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .eq("tipo", "parcela_mensal")
        .eq("origem", "manual")
        .execute()
    )
    updated = 0
    keep_ids = set(keep_pagamento_ids)
    for row in _safe_rows(resp):
        payload = row.get("payload") or {}
        if payload.get("source_module") != "financeiro_cronograma_comissao":
            continue
        if row["id"] in keep_ids:
            continue
        if (row.get("status") or "").lower() == "pago":
            continue
        update_payload = {
            "status": "cancelado",
            "pago_em": None,
            "observacoes": "Pagamento previsto cancelado após reconfiguração do cronograma.",
            "payload": {
                **payload,
                "updated_by_financeiro": actor_id,
                "updated_at_financeiro": _now_iso(),
                "cancelado_por_reconfiguracao": True,
            },
        }
        (
            supa.table("pagamentos")
            .update(update_payload)
            .eq("org_id", org_id)
            .eq("id", row["id"])
            .execute()
        )
        processar_pagamento_para_comissao(
            supa,
            org_id=org_id,
            pagamento_id=row["id"],
            actor_id=actor_id,
        )
        updated += 1
    return updated


def _enrich_pagamento_rows(
    supa: Client,
    org_id: str,
    pagamentos: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not pagamentos:
        return []

    contrato_ids = sorted({str(row["contrato_id"]) for row in pagamentos if row.get("contrato_id")})
    pagamento_ids = sorted({str(row["id"]) for row in pagamentos if row.get("id")})

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

    competencias_by_pagamento: Dict[str, Dict[str, Any]] = {}
    if pagamento_ids:
        resp = (
            supa.table("cota_pagamento_competencias")
            .select("id, pagamento_id, status, gera_comissao, participou_assembleia")
            .eq("org_id", org_id)
            .in_("pagamento_id", pagamento_ids)
            .execute()
        )
        competencias_by_pagamento = {
            row["pagamento_id"]: row
            for row in _safe_rows(resp)
            if row.get("pagamento_id")
        }

    lancamentos_by_pagamento: Dict[str, List[Dict[str, Any]]] = {}
    if pagamento_ids:
        resp = (
            supa.table("comissao_lancamentos")
            .select("id, pagamento_id_origem, status, repasse_status")
            .eq("org_id", org_id)
            .in_("pagamento_id_origem", pagamento_ids)
            .execute()
        )
        for row in _safe_rows(resp):
            pagamento_id = row.get("pagamento_id_origem")
            if not pagamento_id:
                continue
            lancamentos_by_pagamento.setdefault(pagamento_id, []).append(row)

    enriched: List[Dict[str, Any]] = []
    for row in pagamentos:
        contrato = contratos_map.get(str(row.get("contrato_id")))
        cota = (contrato or {}).get("cotas") or {}
        lead = (cota or {}).get("leads") or {}
        comp = competencias_by_pagamento.get(str(row.get("id"))) or {}
        lancamentos = lancamentos_by_pagamento.get(str(row.get("id")), [])
        enriched.append(
            {
                **row,
                "cota_id": (contrato or {}).get("cota_id"),
                "contrato_numero": (contrato or {}).get("numero"),
                "numero_cota": cota.get("numero_cota"),
                "grupo_codigo": cota.get("grupo_codigo"),
                "cliente_nome": lead.get("nome"),
                "competencia_id": comp.get("id"),
                "competencia_status": comp.get("status"),
                "gera_comissao": comp.get("gera_comissao"),
                "participou_assembleia": comp.get("participou_assembleia"),
                "lancamentos_total": len(lancamentos),
                "lancamentos_previstos": sum(1 for item in lancamentos if item.get("status") == "previsto"),
                "lancamentos_disponiveis": sum(1 for item in lancamentos if item.get("status") == "disponivel"),
                "lancamentos_pagos": sum(1 for item in lancamentos if item.get("status") == "pago"),
                "lancamentos_cancelados": sum(1 for item in lancamentos if item.get("status") == "cancelado"),
                "repasses_pendentes": sum(1 for item in lancamentos if item.get("repasse_status") == "pendente"),
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
    inserted = supa.table("pagamentos").insert(payload).execute()
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
        "source_module": (current.get("payload") or {}).get("source_module", "financeiro_operacional"),
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
    cotas_resp = (
        supa.table("cotas")
        .select(
            """
            id,
            status,
            numero_cota,
            grupo_codigo,
            valor_carta,
            administradora_id,
            administradoras ( id, nome ),
            lead_id,
            leads ( id, nome )
            """
        )
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
    )
    cotas = _safe_rows(cotas_resp)

    contratos_resp = (
        supa.table("contratos")
        .select("id, numero, status, cota_id, created_at")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
    )
    contratos = _safe_rows(contratos_resp)
    contrato_by_cota: Dict[str, Dict[str, Any]] = {}
    for contrato in contratos:
        cota_id = contrato.get("cota_id")
        if not cota_id or cota_id in contrato_by_cota:
            continue
        contrato_by_cota[cota_id] = contrato

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
    for cota in cotas:
        lead = cota.get("leads") or {}
        administradora = cota.get("administradoras") or {}
        contrato = contrato_by_cota.get(cota.get("id"))
        config = config_by_cota.get(cota.get("id"))
        parceiro = parceiros_by_cota.get(cota.get("id"))
        selection_id = contrato["id"] if contrato else f"cota:{cota['id']}"
        items.append(
            {
                "selection_id": selection_id,
                "tem_contrato": bool(contrato),
                "contrato_id": contrato["id"] if contrato else "",
                "contrato_numero": (contrato or {}).get("numero"),
                "contrato_status": (contrato or {}).get("status"),
                "cota_status": cota.get("status"),
                "cota_id": cota.get("id"),
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
        raise HTTPException(400, "Numero do contrato é obrigatório")

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
        raise HTTPException(409, "Já existe outro contrato com esse número nesta organização")

    updated_resp = (
        supa.table("contratos")
        .update({"numero": numero})
        .eq("org_id", org_id)
        .eq("id", contrato["id"])
        .execute()
    )
    updated = _safe_one(updated_resp)
    if not updated:
        raise HTTPException(500, "Erro ao atualizar número do contrato")

    return {
        "ok": True,
        "item": {
            "contrato_id": updated["id"],
            "contrato_numero": updated.get("numero"),
        },
    }


def gerar_cronograma_pagamentos_contrato(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
    actor_id: str,
) -> Dict[str, Any]:
    contrato = fetch_contrato_context(supa, org_id, contrato_id)
    cota_id = contrato.get("cota_id")
    if not cota_id:
        raise HTTPException(400, "Contrato sem cota vinculada")

    cota = fetch_cota_context(supa, org_id, cota_id)
    config = fetch_config_by_cota(supa, org_id, cota_id)
    if not config or not config.get("ativo", True):
        raise HTTPException(400, "A cota não possui configuração ativa de comissão")

    regras = fetch_regras(supa, org_id, config["id"])
    if not regras:
        raise HTTPException(400, "A comissão da cota não possui regras configuradas")

    valor_carta = Decimal(str(cota.get("valor_carta") or 0))
    if valor_carta <= 0:
        raise HTTPException(400, "valor_carta precisa ser maior que zero para gerar o cronograma")

    created = 0
    updated = 0
    touched_ids: List[str] = []
    processadas = 0
    competencias_vistas: set[str] = set()

    for regra in sorted(regras, key=lambda row: int(row.get("ordem") or 0)):
        competencia = _resolve_regra_competencia_prevista(
            supa=supa,
            org_id=org_id,
            contrato=contrato,
            cota=cota,
            config=config,
            regra=regra,
        )
        if not competencia:
            continue
        competencia_key = competencia.isoformat()
        if competencia_key in competencias_vistas:
            raise HTTPException(
                409,
                f"Mais de uma regra comercial caiu na mesma competencia {competencia_key}. Revise o cronograma configurado.",
            )
        competencias_vistas.add(competencia_key)

        percentual = Decimal(str(regra.get("percentual_comissao") or 0))
        valor = _money(valor_carta * (percentual / Decimal("100")))
        pagamento, mode = _upsert_pagamento_cronograma(
            supa,
            org_id=org_id,
            actor_id=actor_id,
            contrato=contrato,
            cota=cota,
            regra=regra,
            competencia=competencia,
            valor=valor,
        )
        touched_ids.append(pagamento["id"])
        if mode == "created":
            created += 1
        else:
            updated += 1

        processar_pagamento_para_comissao(
            supa,
            org_id=org_id,
            pagamento_id=pagamento["id"],
            actor_id=actor_id,
        )
        processadas += 1

    cancelled = _cancel_stale_pagamentos_cronograma(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        contrato_id=contrato_id,
        keep_pagamento_ids=touched_ids,
    )

    reprocessar_comissoes_contrato(
        supa,
        org_id=org_id,
        contrato_id=contrato_id,
        actor_id=actor_id,
    )

    return {
        "ok": True,
        "contrato_id": contrato_id,
        "pagamentos_processados": len(touched_ids),
        "pagamentos_criados": created,
        "pagamentos_atualizados": updated,
        "pagamentos_cancelados": cancelled,
        "competencias_processadas": processadas,
    }


def pular_competencia_pagamento(
    supa: Client,
    *,
    org_id: str,
    pagamento_id: str,
    actor_id: str,
) -> Dict[str, Any]:
    pagamento = _get_pagamento_or_404(supa, org_id, pagamento_id)
    contrato_id = pagamento.get("contrato_id")
    competencia_base = _parse_date(pagamento.get("competencia"))
    if not contrato_id or not competencia_base:
        raise HTTPException(400, "Pagamento inválido para reprogramação")

    resp = (
        supa.table("pagamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .eq("tipo", "parcela_mensal")
        .order("competencia")
        .execute()
    )
    rows = _safe_rows(resp)
    candidatos = []
    competencias_imutaveis: set[str] = set()
    for row in rows:
        payload = row.get("payload") or {}
        status = (row.get("status") or "").lower()
        competencia = _parse_date(row.get("competencia"))
        if payload.get("source_module") != "financeiro_cronograma_comissao" or not competencia:
            continue
        if competencia >= competencia_base and status not in {"pago", "cancelado"}:
            candidatos.append(row)
        else:
            competencias_imutaveis.add(competencia.isoformat())

    competencias_planejadas = {_add_months(_parse_date(row.get("competencia")), 1).isoformat() for row in candidatos if _parse_date(row.get("competencia"))}
    conflitos = competencias_planejadas & competencias_imutaveis
    if conflitos:
        raise HTTPException(
            409,
            f"Nao foi possivel pular a competencia porque o deslocamento entraria em conflito com competencias fechadas: {', '.join(sorted(conflitos))}.",
        )

    afetados = 0

    for row in sorted(candidatos, key=lambda item: str(item.get("competencia")), reverse=True):
        payload = row.get("payload") or {}
        competencia = _parse_date(row.get("competencia"))
        if not competencia:
            continue

        novo_vencimento = _parse_date(row.get("vencimento"))
        update_payload = {
            "competencia": _add_months(competencia, 1).isoformat(),
            "vencimento": _add_months(novo_vencimento, 1).isoformat() if novo_vencimento else None,
            "payload": {
                **payload,
                "updated_by_financeiro": actor_id,
                "updated_at_financeiro": _now_iso(),
                "motivo_reprogramacao": "Pulo manual de competencia por ausencia de assembleia/boleto.",
            },
        }
        (
            supa.table("pagamentos")
            .update(update_payload)
            .eq("org_id", org_id)
            .eq("id", row["id"])
            .execute()
        )
        processar_pagamento_para_comissao(
            supa,
            org_id=org_id,
            pagamento_id=row["id"],
            actor_id=actor_id,
        )
        afetados += 1

    reprocessar_comissoes_contrato(
        supa,
        org_id=org_id,
        contrato_id=contrato_id,
        actor_id=actor_id,
    )

    return {
        "ok": True,
        "pagamento_id": pagamento_id,
        "pagamentos_afetados": afetados,
        "message": "Competência pulada e parcelas futuras reprogramadas.",
    }


def cancelar_pagamentos_futuros(
    supa: Client,
    *,
    org_id: str,
    pagamento_id: str,
    actor_id: str,
) -> Dict[str, Any]:
    pagamento = _get_pagamento_or_404(supa, org_id, pagamento_id)
    contrato_id = pagamento.get("contrato_id")
    competencia_base = _parse_date(pagamento.get("competencia"))
    if not contrato_id or not competencia_base:
        raise HTTPException(400, "Pagamento inválido para cancelamento")

    resp = (
        supa.table("pagamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .eq("tipo", "parcela_mensal")
        .order("competencia")
        .execute()
    )
    rows = _safe_rows(resp)
    afetados = 0

    for row in rows:
        payload = row.get("payload") or {}
        status = (row.get("status") or "").lower()
        competencia = _parse_date(row.get("competencia"))
        if payload.get("source_module") != "financeiro_cronograma_comissao":
            continue
        if not competencia or competencia < competencia_base:
            continue
        if status == "pago":
            continue

        update_payload = {
            "status": "cancelado",
            "pago_em": None,
            "payload": {
                **payload,
                "updated_by_financeiro": actor_id,
                "updated_at_financeiro": _now_iso(),
                "motivo_cancelamento_operacional": "Carta cancelada; recebimentos futuros interrompidos.",
            },
            "observacoes": "Cronograma cancelado manualmente a partir desta competência.",
        }
        (
            supa.table("pagamentos")
            .update(update_payload)
            .eq("org_id", org_id)
            .eq("id", row["id"])
            .execute()
        )
        processar_pagamento_para_comissao(
            supa,
            org_id=org_id,
            pagamento_id=row["id"],
            actor_id=actor_id,
        )
        afetados += 1

    reprocessar_comissoes_contrato(
        supa,
        org_id=org_id,
        contrato_id=contrato_id,
        actor_id=actor_id,
    )

    return {
        "ok": True,
        "pagamento_id": pagamento_id,
        "pagamentos_afetados": afetados,
        "message": "Recebimentos futuros cancelados para esta carta.",
    }
