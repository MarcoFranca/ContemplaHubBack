from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from supabase import Client

from app.services.comissao_service import (
    fetch_config_by_cota,
    fetch_cota_context,
    fetch_contrato_context,
    fetch_regras,
)
from app.services.contract_partner_sync_service import (
    insert_audit_log,
    sync_contrato_parceiros_for_contract,
)

MONEY_Q = Decimal("0.01")
PCT_Q = Decimal("0.0001")


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _pct(value: Decimal) -> Decimal:
    return value.quantize(PCT_Q, rounding=ROUND_HALF_UP)


def _safe_rows(resp: Any) -> List[Dict[str, Any]]:
    return getattr(resp, "data", None) or []


def _safe_one(resp: Any) -> Optional[Dict[str, Any]]:
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data


def month_start(d: date) -> date:
    return d.replace(day=1)


def parse_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw)[:10])


def parse_datetime(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def get_pagamento_or_404(supa: Client, org_id: str, pagamento_id: str) -> Dict[str, Any]:
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


def get_competencia_by_id_or_404(supa: Client, org_id: str, competencia_id: str) -> Dict[str, Any]:
    resp = (
        supa.table("cota_pagamento_competencias")
        .select("*")
        .eq("org_id", org_id)
        .eq("id", competencia_id)
        .limit(1)
        .execute()
    )
    rows = _safe_rows(resp)
    if not rows:
        raise HTTPException(404, "Competência não encontrada")
    return rows[0]


def fetch_competencias_contrato(supa: Client, org_id: str, contrato_id: str) -> List[Dict[str, Any]]:
    resp = (
        supa.table("cota_pagamento_competencias")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .order("competencia")
        .execute()
    )
    return _safe_rows(resp)


def fetch_primary_contract_partner(supa: Client, org_id: str, contrato_id: str) -> Optional[Dict[str, Any]]:
    resp = (
        supa.table("contrato_parceiros")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .order("created_at")
        .limit(1)
        .execute()
    )
    rows = _safe_rows(resp)
    return rows[0] if rows else None


def calc_participou_assembleia(
    *,
    competencia: date,
    assembleia_dia: Optional[int],
    pago_em: Optional[datetime],
    vencimento: Optional[date],
) -> Optional[bool]:
    if pago_em is None:
        return False

    if not assembleia_dia:
        return None

    if vencimento and pago_em.date() > vencimento:
        return False

    assembleia_dia = min(max(int(assembleia_dia), 1), 28)
    assembleia_date = date(competencia.year, competencia.month, assembleia_dia)
    return pago_em.date() <= assembleia_date


def _upsert_competencia_by_cota_competencia(
    supa: Client,
    *,
    org_id: str,
    cota_id: str,
    competencia: date,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    existing_resp = (
        supa.table("cota_pagamento_competencias")
        .select("id")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .eq("competencia", competencia.isoformat())
        .limit(1)
        .execute()
    )
    existing = _safe_rows(existing_resp)

    if existing:
        comp_id = existing[0]["id"]
        updated = (
            supa.table("cota_pagamento_competencias")
            .update(payload)
            .eq("org_id", org_id)
            .eq("id", comp_id)
            .execute()
        )
        rows = _safe_rows(updated)
        return rows[0] if rows else {**payload, "id": comp_id}

    inserted = supa.table("cota_pagamento_competencias").insert(payload).execute()
    rows = _safe_rows(inserted)
    if not rows:
        raise HTTPException(500, "Erro ao criar competência de pagamento")
    return rows[0]


def upsert_competencia_from_pagamento(
    supa: Client,
    *,
    org_id: str,
    pagamento_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    pagamento = get_pagamento_or_404(supa, org_id, pagamento_id)

    contrato_id = pagamento.get("contrato_id")
    if not contrato_id:
        raise HTTPException(400, "Pagamento sem contrato_id")

    contrato = fetch_contrato_context(supa, org_id, contrato_id)
    cota_id = contrato.get("cota_id")
    if not cota_id:
        raise HTTPException(400, "Contrato sem cota_id")

    cota = fetch_cota_context(supa, org_id, cota_id)

    competencia = parse_date(pagamento.get("competencia"))
    if not competencia:
        raise HTTPException(400, "Pagamento sem competência")

    vencimento = parse_date(pagamento.get("vencimento"))
    pago_em = parse_datetime(pagamento.get("pago_em"))

    origem = pagamento.get("origem") or "parcela"
    status_pagamento = pagamento.get("status") or "previsto"
    tem_boleto = origem == "parcela"

    pago = status_pagamento == "pago" or pago_em is not None
    pago_no_prazo = None
    if pago and vencimento:
        pago_no_prazo = pago_em.date() <= vencimento if pago_em else None

    participou_assembleia = calc_participou_assembleia(
        competencia=competencia,
        assembleia_dia=cota.get("assembleia_dia"),
        pago_em=pago_em,
        vencimento=vencimento,
    )

    gera_comissao = tem_boleto and pago and status_pagamento != "cancelado"

    if not tem_boleto:
        comp_status = "sem_boleto"
    elif status_pagamento == "cancelado":
        comp_status = "cancelada"
    elif not pago:
        comp_status = "aguardando_pagamento"
    elif pago and participou_assembleia is False:
        comp_status = "paga_sem_assembleia"
    else:
        comp_status = "elegivel_comissao"

    payload = {
        "org_id": org_id,
        "cota_id": cota_id,
        "contrato_id": contrato_id,
        "competencia": competencia.isoformat(),
        "tem_boleto": tem_boleto,
        "boleto_previsto_em": vencimento.isoformat() if vencimento else None,
        "boleto_valor": pagamento.get("valor"),
        "pagamento_id": pagamento_id,
        "pago": pago,
        "pago_em": pago_em.isoformat() if pago_em else None,
        "valor_pago": pagamento.get("valor") if pago else None,
        "vencimento": vencimento.isoformat() if vencimento else None,
        "pago_no_prazo": pago_no_prazo,
        "participou_assembleia": participou_assembleia,
        "motivo_nao_participacao": (
            "Pagamento fora do ciclo da assembleia"
            if pago and participou_assembleia is False
            else None
        ),
        "gera_comissao": gera_comissao,
        "status": comp_status,
        "payload": {
            "origem": "backend_pagamentos",
            "pagamento_id": pagamento_id,
            "status_pagamento": status_pagamento,
            "origem_pagamento": origem,
        },
        "updated_at": datetime.utcnow().isoformat(),
    }

    comp = _upsert_competencia_by_cota_competencia(
        supa,
        org_id=org_id,
        cota_id=cota_id,
        competencia=competencia,
        payload=payload,
    )

    insert_audit_log(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        entity="cota_pagamento_competencias",
        entity_id=comp.get("id"),
        action="upsert_from_pagamento",
        diff={
            "pagamento_id": pagamento_id,
            "contrato_id": contrato_id,
            "competencia": competencia.isoformat(),
            "gera_comissao": gera_comissao,
            "status": comp_status,
        },
    )

    return comp


def _build_empresa_lancamento(
    *,
    org_id: str,
    comp: Dict[str, Any],
    config: Dict[str, Any],
    regra: Dict[str, Any],
    valor_base: Decimal,
    valor_bruto_total: Decimal,
    valor_empresa_bruto: Decimal,
) -> Dict[str, Any]:
    return {
        "org_id": org_id,
        "contrato_id": comp["contrato_id"],
        "cota_id": comp["cota_id"],
        "cota_comissao_config_id": config["id"],
        "regra_id": regra["id"],
        "parceiro_id": None,
        "beneficiario_tipo": "empresa",
        "tipo_evento": regra["tipo_evento"],
        "ordem": int(regra["ordem"]),
        "competencia_prevista": comp["competencia"],
        "competencia_real": comp["competencia"],
        "percentual_base": str(_pct(_dec(regra["percentual_comissao"]))),
        "valor_base": str(_money(valor_base)),
        "valor_bruto": str(_money(valor_empresa_bruto)),
        "imposto_pct": "0.0000",
        "valor_imposto": "0.00",
        "valor_liquido": str(_money(valor_empresa_bruto)),
        "status": "disponivel",
        "liberado_por_evento_em": datetime.utcnow().isoformat(),
        "repasse_status": "nao_aplicavel",
        "pagamento_id_origem": comp.get("pagamento_id"),
        "competencia_id": comp["id"],
        "origem_tipo": "pagamento_parcela",
        "empresa_percentual": str(
            _pct((valor_empresa_bruto / valor_bruto_total) * Decimal("100"))
            if valor_bruto_total > 0
            else Decimal("0")
        ),
        "empresa_valor_bruto": str(_money(valor_empresa_bruto)),
        "empresa_valor_liquido": str(_money(valor_empresa_bruto)),
        "observacoes": "Gerado automaticamente por competência paga",
    }


def _build_parceiro_lancamento(
    *,
    org_id: str,
    comp: Dict[str, Any],
    config: Dict[str, Any],
    regra: Dict[str, Any],
    parceiro_id: str,
    valor_base: Decimal,
    valor_bruto: Decimal,
    imposto_pct: Decimal,
    valor_imposto: Decimal,
    valor_liquido: Decimal,
) -> Dict[str, Any]:
    return {
        "org_id": org_id,
        "contrato_id": comp["contrato_id"],
        "cota_id": comp["cota_id"],
        "cota_comissao_config_id": config["id"],
        "regra_id": regra["id"],
        "parceiro_id": parceiro_id,
        "beneficiario_tipo": "parceiro",
        "tipo_evento": regra["tipo_evento"],
        "ordem": int(regra["ordem"]),
        "competencia_prevista": comp["competencia"],
        "competencia_real": comp["competencia"],
        "percentual_base": str(_pct(_dec(regra["percentual_comissao"]))),
        "valor_base": str(_money(valor_base)),
        "valor_bruto": str(_money(valor_bruto)),
        "imposto_pct": str(_pct(imposto_pct)),
        "valor_imposto": str(_money(valor_imposto)),
        "valor_liquido": str(_money(valor_liquido)),
        "status": "disponivel",
        "liberado_por_evento_em": datetime.utcnow().isoformat(),
        "repasse_status": "pendente",
        "repasse_previsto_em": comp["competencia"],
        "pagamento_id_origem": comp.get("pagamento_id"),
        "competencia_id": comp["id"],
        "origem_tipo": "pagamento_parcela",
        "observacoes": "Repasse gerado automaticamente por competência paga",
    }


def _upsert_lancamento(
    supa: Client,
    *,
    org_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    query = (
        supa.table("comissao_lancamentos")
        .select("id")
        .eq("org_id", org_id)
        .eq("contrato_id", payload["contrato_id"])
        .eq("competencia_id", payload["competencia_id"])
        .eq("regra_id", payload["regra_id"])
        .eq("beneficiario_tipo", payload["beneficiario_tipo"])
    )

    parceiro_id = payload.get("parceiro_id")
    if parceiro_id:
        query = query.eq("parceiro_id", parceiro_id)
    else:
        query = query.is_("parceiro_id", "null")

    existing = _safe_rows(query.limit(1).execute())

    payload["updated_at"] = datetime.utcnow().isoformat()

    if existing:
        lanc_id = existing[0]["id"]
        updated = (
            supa.table("comissao_lancamentos")
            .update(payload)
            .eq("org_id", org_id)
            .eq("id", lanc_id)
            .execute()
        )
        rows = _safe_rows(updated)
        return rows[0] if rows else {**payload, "id": lanc_id}

    payload["created_at"] = datetime.utcnow().isoformat()
    inserted = supa.table("comissao_lancamentos").insert(payload).execute()
    rows = _safe_rows(inserted)
    if not rows:
        raise HTTPException(500, "Erro ao criar lançamento de comissão")
    return rows[0]


def processar_comissao_competencia(
    supa: Client,
    *,
    org_id: str,
    competencia_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    comp = get_competencia_by_id_or_404(supa, org_id, competencia_id)

    if not comp.get("gera_comissao"):
        return {
            "ok": True,
            "processed": False,
            "reason": "Competência sem comissão elegível",
            "competencia": comp,
        }

    contrato = fetch_contrato_context(supa, org_id, comp["contrato_id"])
    cota = fetch_cota_context(supa, org_id, comp["cota_id"])
    config = fetch_config_by_cota(supa, org_id, comp["cota_id"])

    if not config or not config.get("ativo", True):
        return {
            "ok": True,
            "processed": False,
            "reason": "Cota sem configuração ativa de comissão",
            "competencia": comp,
        }

    regras = fetch_regras(supa, org_id, config["id"])
    if not regras:
        return {
            "ok": True,
            "processed": False,
            "reason": "Sem regras de comissão",
            "competencia": comp,
        }

    sync_contrato_parceiros_for_contract(
        supa,
        org_id=org_id,
        contract_id=contrato["id"],
        actor_id=actor_id,
    )
    parceiro = fetch_primary_contract_partner(supa, org_id, contrato["id"])

    valor_base = _dec(cota.get("valor_carta"))
    if valor_base <= 0:
        raise HTTPException(400, "valor_carta da cota precisa ser maior que zero")

    items: List[Dict[str, Any]] = []

    for regra in regras:
        regra_pct = _dec(regra.get("percentual_comissao"))
        valor_bruto_total = _money(valor_base * (regra_pct / Decimal("100")))
        if valor_bruto_total <= 0:
            continue

        parceiro_bruto = Decimal("0")
        parceiro_imposto_pct = Decimal("0")
        parceiro_imposto_valor = Decimal("0")
        parceiro_liquido = Decimal("0")

        if parceiro:
            parceiro_imposto_pct = _dec(parceiro.get("imposto_retido_pct"))
            repasse_percentual = _dec(parceiro.get("repasse_percentual"))
            repasse_valor = _dec(parceiro.get("repasse_valor"))

            if repasse_percentual > 0:
                parceiro_bruto = _money(valor_bruto_total * (repasse_percentual / Decimal("100")))
            elif repasse_valor > 0:
                parceiro_bruto = min(valor_bruto_total, repasse_valor)

            parceiro_imposto_valor = _money(parceiro_bruto * (parceiro_imposto_pct / Decimal("100")))
            parceiro_liquido = _money(parceiro_bruto - parceiro_imposto_valor)

        empresa_bruto = _money(valor_bruto_total - parceiro_bruto)

        empresa_payload = _build_empresa_lancamento(
            org_id=org_id,
            comp=comp,
            config=config,
            regra=regra,
            valor_base=valor_base,
            valor_bruto_total=valor_bruto_total,
            valor_empresa_bruto=empresa_bruto,
        )
        items.append(_upsert_lancamento(supa, org_id=org_id, payload=empresa_payload))

        if parceiro and parceiro_bruto > 0:
            parceiro_payload = _build_parceiro_lancamento(
                org_id=org_id,
                comp=comp,
                config=config,
                regra=regra,
                parceiro_id=parceiro["parceiro_id"],
                valor_base=valor_base,
                valor_bruto=parceiro_bruto,
                imposto_pct=parceiro_imposto_pct,
                valor_imposto=parceiro_imposto_valor,
                valor_liquido=parceiro_liquido,
            )
            items.append(_upsert_lancamento(supa, org_id=org_id, payload=parceiro_payload))

    insert_audit_log(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        entity="comissao_lancamentos",
        entity_id=comp["id"],
        action="process_competencia",
        diff={
            "competencia_id": comp["id"],
            "contrato_id": comp["contrato_id"],
            "total_itens": len(items),
        },
    )

    return {
        "ok": True,
        "processed": True,
        "competencia": comp,
        "items": items,
        "total_itens": len(items),
    }


def processar_pagamento_para_comissao(
    supa: Client,
    *,
    org_id: str,
    pagamento_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    comp = upsert_competencia_from_pagamento(
        supa,
        org_id=org_id,
        pagamento_id=pagamento_id,
        actor_id=actor_id,
    )
    result = processar_comissao_competencia(
        supa,
        org_id=org_id,
        competencia_id=comp["id"],
        actor_id=actor_id,
    )
    return {
        "ok": True,
        "competencia": comp,
        "processamento": result,
    }


def reprocessar_comissoes_contrato(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    contrato = fetch_contrato_context(supa, org_id, contrato_id)

    (
        supa.table("comissao_lancamentos")
        .delete()
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .eq("origem_tipo", "pagamento_parcela")
        .execute()
    )

    sync_contrato_parceiros_for_contract(
        supa,
        org_id=org_id,
        contract_id=contrato_id,
        actor_id=actor_id,
    )

    competencias = fetch_competencias_contrato(supa, org_id, contrato_id)
    processadas = []

    for comp in competencias:
        if comp.get("gera_comissao"):
            result = processar_comissao_competencia(
                supa,
                org_id=org_id,
                competencia_id=comp["id"],
                actor_id=actor_id,
            )
            processadas.append({
                "competencia_id": comp["id"],
                "processed": result.get("processed", False),
                "total_itens": result.get("total_itens", 0),
            })

    insert_audit_log(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        entity="contratos",
        entity_id=contrato["id"],
        action="reprocessar_comissoes_contrato",
        diff={"competencias_processadas": len(processadas)},
    )

    return {
        "ok": True,
        "contrato": contrato,
        "competencias_processadas": processadas,
    }

def listar_competencias_contrato(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
) -> Dict[str, Any]:
    contrato = fetch_contrato_context(supa, org_id, contrato_id)

    resp = (
        supa.table("cota_pagamento_competencias")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .order("competencia")
        .execute()
    )
    competencias = _safe_rows(resp)

    return {
        "ok": True,
        "contrato": contrato,
        "items": competencias,
        "total": len(competencias),
    }


def resumo_financeiro_contrato(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
) -> Dict[str, Any]:
    contrato = fetch_contrato_context(supa, org_id, contrato_id)

    resp = (
        supa.table("comissao_lancamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .order("competencia_real")
        .order("ordem")
        .execute()
    )
    rows = _safe_rows(resp)

    total_bruto = Decimal("0")
    total_imposto = Decimal("0")
    total_liquido = Decimal("0")

    total_empresa_bruto = Decimal("0")
    total_empresa_liquido = Decimal("0")

    total_parceiro_bruto = Decimal("0")
    total_parceiro_imposto = Decimal("0")
    total_parceiro_liquido = Decimal("0")

    por_status: Dict[str, int] = {}
    por_repasse_status: Dict[str, int] = {}

    for row in rows:
        bruto = _dec(row.get("valor_bruto"))
        imposto = _dec(row.get("valor_imposto"))
        liquido = _dec(row.get("valor_liquido"))

        total_bruto += bruto
        total_imposto += imposto
        total_liquido += liquido

        status = row.get("status") or "sem_status"
        por_status[status] = por_status.get(status, 0) + 1

        repasse_status = row.get("repasse_status") or "sem_repasse_status"
        por_repasse_status[repasse_status] = por_repasse_status.get(repasse_status, 0) + 1

        if row.get("beneficiario_tipo") == "empresa":
            total_empresa_bruto += bruto
            total_empresa_liquido += liquido

        elif row.get("beneficiario_tipo") == "parceiro":
            total_parceiro_bruto += bruto
            total_parceiro_imposto += imposto
            total_parceiro_liquido += liquido

    return {
        "ok": True,
        "contrato": contrato,
        "totais": {
            "total_bruto": str(_money(total_bruto)),
            "total_imposto": str(_money(total_imposto)),
            "total_liquido": str(_money(total_liquido)),
            "empresa": {
                "bruto": str(_money(total_empresa_bruto)),
                "liquido": str(_money(total_empresa_liquido)),
            },
            "parceiro": {
                "bruto": str(_money(total_parceiro_bruto)),
                "imposto": str(_money(total_parceiro_imposto)),
                "liquido": str(_money(total_parceiro_liquido)),
            },
        },
        "quantidades": {
            "lancamentos": len(rows),
            "por_status": por_status,
            "por_repasse_status": por_repasse_status,
        },
        "items": rows,
    }


def timeline_contrato(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
) -> Dict[str, Any]:
    contrato = fetch_contrato_context(supa, org_id, contrato_id)

    pagamentos_resp = (
        supa.table("pagamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .order("competencia")
        .execute()
    )
    pagamentos = _safe_rows(pagamentos_resp)

    competencias_resp = (
        supa.table("cota_pagamento_competencias")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .order("competencia")
        .execute()
    )
    competencias = _safe_rows(competencias_resp)

    lancamentos_resp = (
        supa.table("comissao_lancamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .order("competencia_real")
        .order("ordem")
        .execute()
    )
    lancamentos = _safe_rows(lancamentos_resp)

    eventos = []

    for p in pagamentos:
        eventos.append({
            "tipo": "pagamento",
            "data_ref": p.get("competencia"),
            "timestamp": p.get("pago_em") or p.get("created_at"),
            "titulo": "Pagamento registrado",
            "payload": p,
        })

    for c in competencias:
        eventos.append({
            "tipo": "competencia",
            "data_ref": c.get("competencia"),
            "timestamp": c.get("updated_at") or c.get("created_at"),
            "titulo": "Competência atualizada",
            "payload": c,
        })

    for l in lancamentos:
        eventos.append({
            "tipo": "lancamento_comissao",
            "data_ref": l.get("competencia_real") or l.get("competencia_prevista"),
            "timestamp": l.get("repasse_pago_em") or l.get("pago_em") or l.get("updated_at") or l.get("created_at"),
            "titulo": (
                "Repasse parceiro pago"
                if l.get("beneficiario_tipo") == "parceiro" and l.get("repasse_status") == "pago"
                else "Lançamento de comissão"
            ),
            "payload": l,
        })

    eventos.sort(key=lambda e: (e.get("timestamp") or "", e.get("data_ref") or ""))

    return {
        "ok": True,
        "contrato": contrato,
        "items": eventos,
        "total": len(eventos),
    }