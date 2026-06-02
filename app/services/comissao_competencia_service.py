from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from supabase import Client

from app.services.comissao_service import (
    fetch_config_by_cota,
    fetch_cota_context,
    fetch_contrato_context,
    fetch_parceiros_da_cota,
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


def add_months(d: date, months: int) -> date:
    month_index = (d.month - 1) + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def add_months_month_start(d: date, months: int) -> date:
    return month_start(add_months(month_start(d), months))


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


def fetch_active_cota_partners(supa: Client, org_id: str, cota_id: str) -> List[Dict[str, Any]]:
    parceiros = fetch_parceiros_da_cota(supa, org_id, cota_id)
    return [row for row in parceiros if row.get("ativo", True) and row.get("parceiro_id")]


def _compute_primeira_cobranca_valida(cota: Dict[str, Any], config: Dict[str, Any]) -> date:
    adesao = parse_date(cota.get("data_adesao"))
    if not adesao:
        raise HTTPException(400, "A cota precisa de data_adesao para calcular a comissão")

    assembleia_dia = cota.get("assembleia_dia")
    furo_meses = config.get("furo_meses_override")
    if furo_meses is None:
        furo_meses = cota.get("furo_meses") or 0
    furo_meses = int(furo_meses or 0)

    missed_cycle = bool(assembleia_dia and adesao.day > int(assembleia_dia))
    months_forward = 1 + furo_meses + (1 if missed_cycle else 0)
    return add_months_month_start(adesao, months_forward)


def _resolve_competencia_base(cota: Dict[str, Any], config: Dict[str, Any]) -> Optional[date]:
    modo = config.get("competencia_base_modo")
    manual = parse_date(config.get("competencia_base_manual"))
    if modo == "manual" and manual:
        return month_start(manual)

    primeira_regra = config.get("primeira_competencia_regra") or "mes_adesao"
    if primeira_regra == "manual":
        return month_start(manual) if manual else None

    adesao = parse_date(cota.get("data_adesao"))
    if not adesao:
        raise HTTPException(400, "A cota precisa de data_adesao para calcular a comissão")

    if primeira_regra == "mes_adesao":
        return month_start(adesao)

    return _compute_primeira_cobranca_valida(cota, config)


def _resolve_contemplacao_competencia(supa: Client, org_id: str, contrato: Dict[str, Any], cota_id: str) -> Optional[date]:
    contrato_data = parse_date(contrato.get("data_contemplacao"))
    if contrato_data:
        return month_start(contrato_data)

    resp = (
        supa.table("contemplacoes")
        .select("data")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .order("data", desc=True)
        .limit(1)
        .execute()
    )
    rows = _safe_rows(resp)
    if not rows:
        return None
    return month_start(parse_date(rows[0].get("data")))


def _resolve_regra_competencia_prevista(
    *,
    supa: Client,
    org_id: str,
    contrato: Dict[str, Any],
    cota: Dict[str, Any],
    config: Dict[str, Any],
    regra: Dict[str, Any],
) -> Optional[date]:
    tipo = regra.get("tipo_evento")
    offset = int(regra.get("offset_meses") or 0)
    adesao = parse_date(cota.get("data_adesao"))
    base_competencia = _resolve_competencia_base(cota, config)

    if tipo == "adesao":
        if not adesao:
            return None
        return add_months_month_start(adesao, offset)

    if tipo == "primeira_cobranca_valida":
        return add_months_month_start(_compute_primeira_cobranca_valida(cota, config), offset)

    if tipo == "proxima_cobranca":
        if not base_competencia:
            return None
        return add_months_month_start(base_competencia, offset)

    if tipo == "manual":
        if not base_competencia:
            return None
        return add_months_month_start(base_competencia, offset)

    if tipo == "contemplacao":
        contemplacao = _resolve_contemplacao_competencia(supa, org_id, contrato, cota["id"])
        if not contemplacao:
            return None
        return add_months_month_start(contemplacao, offset)

    return None


def _find_matching_regra_for_competencia(
    *,
    supa: Client,
    org_id: str,
    contrato: Dict[str, Any],
    cota: Dict[str, Any],
    config: Dict[str, Any],
    regras: List[Dict[str, Any]],
    competencia: date,
) -> Optional[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for regra in sorted(regras, key=lambda row: int(row.get("ordem") or 0)):
        prevista = _resolve_regra_competencia_prevista(
            supa=supa,
            org_id=org_id,
            contrato=contrato,
            cota=cota,
            config=config,
            regra=regra,
        )
        if prevista and prevista == competencia:
            matches.append(regra)

    if len(matches) > 1:
        raise HTTPException(
            409,
            f"Mais de uma regra de comissão corresponde à competência {competencia.isoformat()} para a cota {cota['id']}",
        )
    return matches[0] if matches else None


def _rule_requires_assembleia(regra: Dict[str, Any], config: Dict[str, Any]) -> bool:
    tipo = regra.get("tipo_evento")
    if tipo in {"adesao", "contemplacao"}:
        return False
    if tipo == "manual" and config.get("primeira_competencia_regra") == "manual":
        return True
    return True


def _determine_target_status(
    *,
    comp: Dict[str, Any],
    regra: Dict[str, Any],
    config: Dict[str, Any],
) -> str:
    payload = comp.get("payload") or {}
    status_pagamento = payload.get("status_pagamento")

    if status_pagamento in {"cancelado", "inadimplente"}:
        return "cancelado"
    if not comp.get("gera_comissao"):
        return "previsto"
    if _rule_requires_assembleia(regra, config) and comp.get("participou_assembleia") is False:
        return "previsto"
    return "disponivel"


def _repasse_status_for_target(status: str) -> str:
    if status == "cancelado":
        return "cancelado"
    if status == "disponivel":
        return "pendente"
    return "pendente"


def _payload_diverges(current: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    tracked_fields = (
        "competencia_id",
        "competencia_prevista",
        "competencia_real",
        "valor_bruto",
        "valor_liquido",
        "valor_imposto",
        "status",
        "repasse_status",
        "pagamento_id_origem",
    )
    for field in tracked_fields:
        if str(current.get(field)) != str(payload.get(field)):
            return True
    return False


def _find_existing_lancamentos_for_competencia_regra(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
    competencia_id: str,
    regra_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    query = (
        supa.table("comissao_lancamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .eq("competencia_id", competencia_id)
        .eq("origem_tipo", "pagamento_parcela")
    )
    if regra_id:
        query = query.eq("regra_id", regra_id)
    return _safe_rows(query.execute())


def _cancel_or_block_existing_lancamentos(
    supa: Client,
    *,
    org_id: str,
    lancamentos: List[Dict[str, Any]],
    target_status: str,
    observacao: str,
) -> List[Dict[str, Any]]:
    updated_items: List[Dict[str, Any]] = []
    for lanc in lancamentos:
        if lanc.get("status") == "pago":
            updated_items.append(lanc)
            continue
        if lanc.get("beneficiario_tipo") == "parceiro" and lanc.get("repasse_status") == "pago":
            updated_items.append(lanc)
            continue

        update_payload: Dict[str, Any] = {
            "status": target_status,
            "observacoes": observacao,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if lanc.get("beneficiario_tipo") == "parceiro":
            update_payload["repasse_status"] = _repasse_status_for_target(target_status)
            if target_status != "disponivel":
                update_payload["repasse_previsto_em"] = None
        if target_status != "disponivel":
            update_payload["liberado_por_evento_em"] = None
            update_payload["competencia_real"] = None

        resp = (
            supa.table("comissao_lancamentos")
            .update(update_payload)
            .eq("org_id", org_id)
            .eq("id", lanc["id"])
            .execute()
        )
        updated_items.append(_safe_one(resp) or {**lanc, **update_payload})
    return updated_items


def _build_empresa_lancamento(
    *,
    org_id: str,
    comp: Dict[str, Any],
    config: Dict[str, Any],
    regra: Dict[str, Any],
    competencia_prevista: date,
    valor_base: Decimal,
    valor_bruto_total: Decimal,
    valor_empresa_bruto: Decimal,
    status: str,
) -> Dict[str, Any]:
    now_iso = datetime.utcnow().isoformat()
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
        "competencia_prevista": competencia_prevista.isoformat(),
        "competencia_real": comp["competencia"] if status == "disponivel" else None,
        "percentual_base": str(_pct(_dec(regra["percentual_comissao"]))),
        "valor_base": str(_money(valor_base)),
        "valor_bruto": str(_money(valor_empresa_bruto)),
        "imposto_pct": "0.0000",
        "valor_imposto": "0.00",
        "valor_liquido": str(_money(valor_empresa_bruto)),
        "status": status,
        "liberado_por_evento_em": now_iso if status == "disponivel" else None,
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
        "observacoes": (
            "Gerado automaticamente por competência elegível"
            if status == "disponivel"
            else "Mantido bloqueado até a competência ficar elegível"
        ),
    }


def _build_parceiro_lancamento(
    *,
    org_id: str,
    comp: Dict[str, Any],
    config: Dict[str, Any],
    regra: Dict[str, Any],
    parceiro: Dict[str, Any],
    competencia_prevista: date,
    valor_base: Decimal,
    valor_bruto: Decimal,
    imposto_pct: Decimal,
    valor_imposto: Decimal,
    valor_liquido: Decimal,
    status: str,
) -> Dict[str, Any]:
    now_iso = datetime.utcnow().isoformat()
    return {
        "org_id": org_id,
        "contrato_id": comp["contrato_id"],
        "cota_id": comp["cota_id"],
        "cota_comissao_config_id": config["id"],
        "regra_id": regra["id"],
        "parceiro_id": parceiro["parceiro_id"],
        "beneficiario_tipo": "parceiro",
        "tipo_evento": regra["tipo_evento"],
        "ordem": int(regra["ordem"]),
        "competencia_prevista": competencia_prevista.isoformat(),
        "competencia_real": comp["competencia"] if status == "disponivel" else None,
        "percentual_base": str(_pct(_dec(regra["percentual_comissao"]))),
        "valor_base": str(_money(valor_base)),
        "valor_bruto": str(_money(valor_bruto)),
        "imposto_pct": str(_pct(imposto_pct)),
        "valor_imposto": str(_money(valor_imposto)),
        "valor_liquido": str(_money(valor_liquido)),
        "status": status,
        "liberado_por_evento_em": now_iso if status == "disponivel" else None,
        "repasse_status": _repasse_status_for_target(status),
        "repasse_previsto_em": comp["competencia"] if status == "disponivel" else None,
        "pagamento_id_origem": comp.get("pagamento_id"),
        "competencia_id": comp["id"],
        "origem_tipo": "pagamento_parcela",
        "observacoes": (
            "Repasse derivado da mesma parcela da comissão"
            if status == "disponivel"
            else "Repasse bloqueado até a comissão da competência ser liberada"
        ),
    }


def _upsert_lancamento(
    supa: Client,
    *,
    org_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    query = (
        supa.table("comissao_lancamentos")
        .select("*")
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
        current = existing[0]
        if current.get("status") == "pago":
            if _payload_diverges(current, payload):
                insert_audit_log(
                    supa,
                    org_id=org_id,
                    actor_id=None,
                    entity="comissao_lancamentos",
                    entity_id=current["id"],
                    action="paid_lancamento_conflict_preserved",
                    diff={
                        "current": {
                            "id": current.get("id"),
                            "status": current.get("status"),
                            "competencia_id": current.get("competencia_id"),
                            "regra_id": current.get("regra_id"),
                            "valor_bruto": current.get("valor_bruto"),
                            "valor_liquido": current.get("valor_liquido"),
                        },
                        "attempted": {
                            "status": payload.get("status"),
                            "competencia_id": payload.get("competencia_id"),
                            "regra_id": payload.get("regra_id"),
                            "valor_bruto": payload.get("valor_bruto"),
                            "valor_liquido": payload.get("valor_liquido"),
                        },
                        "reason": "Paid launch preserved without destructive overwrite.",
                    },
                )
            return current
        if current.get("beneficiario_tipo") == "parceiro" and current.get("repasse_status") == "pago":
            if _payload_diverges(current, payload):
                insert_audit_log(
                    supa,
                    org_id=org_id,
                    actor_id=None,
                    entity="comissao_lancamentos",
                    entity_id=current["id"],
                    action="paid_repasse_conflict_preserved",
                    diff={
                        "current": {
                            "id": current.get("id"),
                            "status": current.get("status"),
                            "repasse_status": current.get("repasse_status"),
                            "competencia_id": current.get("competencia_id"),
                            "regra_id": current.get("regra_id"),
                        },
                        "attempted": {
                            "status": payload.get("status"),
                            "repasse_status": payload.get("repasse_status"),
                            "competencia_id": payload.get("competencia_id"),
                            "regra_id": payload.get("regra_id"),
                        },
                        "reason": "Paid partner transfer preserved without destructive overwrite.",
                    },
                )
            return current

        updated = (
            supa.table("comissao_lancamentos")
            .update(payload)
            .eq("org_id", org_id)
            .eq("id", current["id"])
            .execute()
        )
        return _safe_one(updated) or {**current, **payload}

    payload["created_at"] = datetime.utcnow().isoformat()
    inserted = supa.table("comissao_lancamentos").insert(payload).execute()
    row = _safe_one(inserted)
    if not row:
        raise HTTPException(500, "Erro ao criar lançamento de comissão")
    return row


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
    status_pagamento = (pagamento.get("status") or "previsto").lower()
    tem_boleto = origem in {"parcela", "manual"}

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

    gera_comissao = tem_boleto and pago and status_pagamento not in {"cancelado", "inadimplente"}

    if not tem_boleto:
        comp_status = "sem_boleto"
    elif status_pagamento == "cancelado":
        comp_status = "cancelada"
    elif status_pagamento == "inadimplente":
        comp_status = "inadimplente"
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


def processar_comissao_competencia(
    supa: Client,
    *,
    org_id: str,
    competencia_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    comp = get_competencia_by_id_or_404(supa, org_id, competencia_id)
    contrato = fetch_contrato_context(supa, org_id, comp["contrato_id"])
    cota = fetch_cota_context(supa, org_id, comp["cota_id"])
    config = fetch_config_by_cota(supa, org_id, comp["cota_id"])

    if not config or not config.get("ativo", True):
        existing = _find_existing_lancamentos_for_competencia_regra(
            supa,
            org_id=org_id,
            contrato_id=comp["contrato_id"],
            competencia_id=comp["id"],
        )
        _cancel_or_block_existing_lancamentos(
            supa,
            org_id=org_id,
            lancamentos=existing,
            target_status="previsto",
            observacao="Competência sem configuração ativa de comissão.",
        )
        return {
            "ok": True,
            "processed": False,
            "reason": "Cota sem configuração ativa de comissão",
            "competencia": comp,
        }

    regras = fetch_regras(supa, org_id, config["id"])
    if not regras:
        existing = _find_existing_lancamentos_for_competencia_regra(
            supa,
            org_id=org_id,
            contrato_id=comp["contrato_id"],
            competencia_id=comp["id"],
        )
        _cancel_or_block_existing_lancamentos(
            supa,
            org_id=org_id,
            lancamentos=existing,
            target_status="previsto",
            observacao="Competência sem regras de comissão configuradas.",
        )
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

    competencia_ref = parse_date(comp.get("competencia"))
    if not competencia_ref:
        raise HTTPException(400, "Competência inválida")

    regra = _find_matching_regra_for_competencia(
        supa=supa,
        org_id=org_id,
        contrato=contrato,
        cota=cota,
        config=config,
        regras=regras,
        competencia=competencia_ref,
    )

    existing_for_comp = _find_existing_lancamentos_for_competencia_regra(
        supa,
        org_id=org_id,
        contrato_id=comp["contrato_id"],
        competencia_id=comp["id"],
    )

    if not regra:
        blocked = _cancel_or_block_existing_lancamentos(
            supa,
            org_id=org_id,
            lancamentos=existing_for_comp,
            target_status="previsto",
            observacao="Nenhuma parcela da comissão corresponde a esta competência.",
        )
        return {
            "ok": True,
            "processed": False,
            "reason": "Nenhuma regra corresponde à competência",
            "competencia": comp,
            "items": blocked,
            "total_itens": len(blocked),
        }

    target_status = _determine_target_status(comp=comp, regra=regra, config=config)
    existing_other_rules = [row for row in existing_for_comp if row.get("regra_id") != regra["id"]]
    blocked_items = _cancel_or_block_existing_lancamentos(
        supa,
        org_id=org_id,
        lancamentos=existing_other_rules,
        target_status="cancelado",
        observacao="Lançamento descontinuado após remapeamento da competência para outra parcela.",
    )

    valor_base = _dec(cota.get("valor_carta"))
    if valor_base <= 0:
        raise HTTPException(400, "valor_carta da cota precisa ser maior que zero")

    total_pct = _dec(config.get("percentual_total"))
    if total_pct <= 0:
        raise HTTPException(400, "percentual_total da comissão precisa ser maior que zero")

    regra_pct = _dec(regra.get("percentual_comissao"))
    valor_bruto_total = _money(valor_base * (regra_pct / Decimal("100")))
    parceiros = fetch_active_cota_partners(supa, org_id, comp["cota_id"])

    parceiro_rows: List[Tuple[Dict[str, Any], Decimal, Decimal, Decimal, Decimal]] = []
    total_parceiros_bruto = Decimal("0")

    for parceiro in parceiros:
        parceiro_pct_total = _dec(parceiro.get("percentual_parceiro"))
        if parceiro_pct_total <= 0:
            continue
        ratio = parceiro_pct_total / total_pct
        parceiro_pct_regra = _pct(regra_pct * ratio)
        valor_bruto = _money(valor_base * (parceiro_pct_regra / Decimal("100")))
        imposto_pct = _dec(parceiro.get("imposto_retido_pct"))
        valor_imposto = _money(valor_bruto * (imposto_pct / Decimal("100")))
        valor_liquido = _money(valor_bruto - valor_imposto)
        total_parceiros_bruto += valor_bruto
        parceiro_rows.append((parceiro, valor_bruto, imposto_pct, valor_imposto, valor_liquido))

    valor_empresa_bruto = _money(valor_bruto_total - total_parceiros_bruto)
    competencia_prevista = _resolve_regra_competencia_prevista(
        supa=supa,
        org_id=org_id,
        contrato=contrato,
        cota=cota,
        config=config,
        regra=regra,
    )
    if not competencia_prevista:
        raise HTTPException(400, "Não foi possível determinar a competência da parcela de comissão")

    items: List[Dict[str, Any]] = []
    empresa_payload = _build_empresa_lancamento(
        org_id=org_id,
        comp=comp,
        config=config,
        regra=regra,
        competencia_prevista=competencia_prevista,
        valor_base=valor_base,
        valor_bruto_total=valor_bruto_total,
        valor_empresa_bruto=valor_empresa_bruto,
        status=target_status,
    )
    items.append(_upsert_lancamento(supa, org_id=org_id, payload=empresa_payload))

    for parceiro, valor_bruto, imposto_pct, valor_imposto, valor_liquido in parceiro_rows:
        parceiro_payload = _build_parceiro_lancamento(
            org_id=org_id,
            comp=comp,
            config=config,
            regra=regra,
            parceiro=parceiro,
            competencia_prevista=competencia_prevista,
            valor_base=valor_base,
            valor_bruto=valor_bruto,
            imposto_pct=imposto_pct,
            valor_imposto=valor_imposto,
            valor_liquido=valor_liquido,
            status=target_status,
        )
        items.append(_upsert_lancamento(supa, org_id=org_id, payload=parceiro_payload))

    if target_status != "disponivel":
        items = _cancel_or_block_existing_lancamentos(
            supa,
            org_id=org_id,
            lancamentos=items,
            target_status=target_status,
            observacao=(
                "Competência paga sem assembleia: comissão mantida bloqueada."
                if comp.get("status") == "paga_sem_assembleia"
                else "Competência ainda não elegível para liberar comissão."
            ),
        )

    total_items = len(blocked_items) + len(items)
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
            "regra_id": regra["id"],
            "status_alvo": target_status,
            "total_itens": total_items,
        },
    )

    return {
        "ok": True,
        "processed": True,
        "competencia": comp,
        "regra": regra,
        "items": blocked_items + items,
        "total_itens": total_items,
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


def _cancel_stale_launches_for_contract(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
    active_competencia_ids: List[str],
) -> int:
    resp = (
        supa.table("comissao_lancamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .eq("origem_tipo", "pagamento_parcela")
        .execute()
    )
    rows = _safe_rows(resp)
    updated = 0
    active_ids = set(active_competencia_ids)
    for row in rows:
        if row.get("competencia_id") in active_ids:
            continue
        if row.get("status") == "pago":
            continue
        if row.get("beneficiario_tipo") == "parceiro" and row.get("repasse_status") == "pago":
            continue
        (
            supa.table("comissao_lancamentos")
            .update(
                {
                    "status": "cancelado",
                    "repasse_status": "cancelado" if row.get("beneficiario_tipo") == "parceiro" else row.get("repasse_status"),
                    "observacoes": "Lançamento cancelado em reprocessamento por não existir mais competência ativa correspondente.",
                    "updated_at": datetime.utcnow().isoformat(),
                }
            )
            .eq("org_id", org_id)
            .eq("id", row["id"])
            .execute()
        )
        updated += 1
    return updated


def reprocessar_comissoes_contrato(
    supa: Client,
    *,
    org_id: str,
    contrato_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    contrato = fetch_contrato_context(supa, org_id, contrato_id)

    sync_contrato_parceiros_for_contract(
        supa,
        org_id=org_id,
        contract_id=contrato_id,
        actor_id=actor_id,
    )

    competencias = fetch_competencias_contrato(supa, org_id, contrato_id)
    processadas = []

    for comp in competencias:
        result = processar_comissao_competencia(
            supa,
            org_id=org_id,
            competencia_id=comp["id"],
            actor_id=actor_id,
        )
        processadas.append(
            {
                "competencia_id": comp["id"],
                "processed": result.get("processed", False),
                "total_itens": result.get("total_itens", 0),
                "reason": result.get("reason"),
            }
        )

    stale_cancelled = _cancel_stale_launches_for_contract(
        supa,
        org_id=org_id,
        contrato_id=contrato_id,
        active_competencia_ids=[row["id"] for row in competencias],
    )

    insert_audit_log(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        entity="contratos",
        entity_id=contrato["id"],
        action="reprocessar_comissoes_contrato",
        diff={
            "competencias_processadas": len(processadas),
            "lancamentos_cancelados_sem_competencia": stale_cancelled,
        },
    )

    return {
        "ok": True,
        "contrato": contrato,
        "competencias_processadas": processadas,
        "lancamentos_cancelados_sem_competencia": stale_cancelled,
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
        eventos.append(
            {
                "tipo": "pagamento",
                "data_ref": p.get("competencia"),
                "timestamp": p.get("pago_em") or p.get("created_at"),
                "titulo": "Pagamento registrado",
                "payload": p,
            }
        )

    for c in competencias:
        eventos.append(
            {
                "tipo": "competencia",
                "data_ref": c.get("competencia"),
                "timestamp": c.get("updated_at") or c.get("created_at"),
                "titulo": "Competência atualizada",
                "payload": c,
            }
        )

    for l in lancamentos:
        eventos.append(
            {
                "tipo": "lancamento_comissao",
                "data_ref": l.get("competencia_real") or l.get("competencia_prevista"),
                "timestamp": l.get("repasse_pago_em") or l.get("pago_em") or l.get("updated_at") or l.get("created_at"),
                "titulo": (
                    "Repasse parceiro pago"
                    if l.get("beneficiario_tipo") == "parceiro" and l.get("repasse_status") == "pago"
                    else "Lançamento de comissão"
                ),
                "payload": l,
            }
        )

    eventos.sort(key=lambda e: (e.get("timestamp") or "", e.get("data_ref") or ""))

    return {
        "ok": True,
        "contrato": contrato,
        "items": eventos,
        "total": len(eventos),
    }
