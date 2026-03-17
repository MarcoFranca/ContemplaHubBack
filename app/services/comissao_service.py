from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from supabase import Client

from app.schemas.comissoes import CotaComissaoConfigUpsertIn

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


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, months: int) -> date:
    base_month = d.month - 1 + months
    year = d.year + base_month // 12
    month = base_month % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def add_months_month_start(d: date, months: int) -> date:
    return month_start(add_months(month_start(d), months))


@dataclass
class CronogramaBase:
    adesao: date
    primeira_cobranca_valida: date


def parse_iso_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw))


def get_org_record_or_404(supa: Client, table: str, org_id: str, record_id: str, columns: str = "*") -> Dict[str, Any]:
    resp = (
        supa.table(table)
        .select(columns)
        .eq("org_id", org_id)
        .eq("id", record_id)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(404, f"Registro não encontrado em {table}")
    return rows[0]


def fetch_cota_context(supa: Client, org_id: str, cota_id: str) -> Dict[str, Any]:
    return get_org_record_or_404(
        supa,
        "cotas",
        org_id,
        cota_id,
        columns="id, org_id, numero_cota, grupo_codigo, valor_carta, data_adesao, assembleia_dia, furo_meses, status",
    )


def fetch_contrato_context(supa: Client, org_id: str, contrato_id: str) -> Dict[str, Any]:
    return get_org_record_or_404(
        supa,
        "contratos",
        org_id,
        contrato_id,
        columns="id, org_id, cota_id, numero, status, data_assinatura, data_contemplacao, created_at",
    )


def fetch_config_by_cota(supa: Client, org_id: str, cota_id: str) -> Optional[Dict[str, Any]]:
    resp = (
        supa.table("cota_comissao_config")
        .select("*")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def fetch_regras(supa: Client, org_id: str, config_id: str) -> List[Dict[str, Any]]:
    resp = (
        supa.table("cota_comissao_regras")
        .select("*")
        .eq("org_id", org_id)
        .eq("cota_comissao_config_id", config_id)
        .order("ordem")
        .execute()
    )
    return getattr(resp, "data", None) or []


def fetch_parceiros_da_cota(supa: Client, org_id: str, cota_id: str) -> List[Dict[str, Any]]:
    resp = (
        supa.table("cota_comissao_parceiros")
        .select("*, parceiros_corretores(id, nome, ativo)")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .order("created_at")
        .execute()
    )
    return getattr(resp, "data", None) or []


def validate_partner_ids(supa: Client, org_id: str, payload: CotaComissaoConfigUpsertIn) -> None:
    for partner in payload.parceiros:
        get_org_record_or_404(supa, "parceiros_corretores", org_id, partner.parceiro_id, columns="id, ativo")


def compute_cronograma_base(cota: Dict[str, Any], config: Dict[str, Any]) -> CronogramaBase:
    adesao = parse_iso_date(cota.get("data_adesao"))
    if not adesao:
        raise HTTPException(400, "A cota precisa de data_adesao para projetar comissão")

    assembleia_dia = cota.get("assembleia_dia")
    furo_meses = config.get("furo_meses_override")
    if furo_meses is None:
        furo_meses = cota.get("furo_meses") or 0
    furo_meses = int(furo_meses or 0)

    missed_cycle = bool(assembleia_dia and adesao.day > int(assembleia_dia))
    months_forward = 1 + furo_meses + (1 if missed_cycle else 0)
    primeira_cobranca = add_months_month_start(adesao, months_forward)

    return CronogramaBase(adesao=month_start(adesao), primeira_cobranca_valida=primeira_cobranca)


def determine_competencia_prevista(
    regra: Dict[str, Any],
    cronograma: CronogramaBase,
    config: Dict[str, Any],
    contemplacao_data: Optional[date],
) -> Optional[date]:
    tipo = regra["tipo_evento"]
    offset = int(regra.get("offset_meses") or 0)

    if tipo == "adesao":
        return add_months_month_start(cronograma.adesao, offset)

    if tipo == "primeira_cobranca_valida":
        return add_months_month_start(cronograma.primeira_cobranca_valida, offset)

    if tipo == "proxima_cobranca":
        return add_months_month_start(cronograma.primeira_cobranca_valida, offset)

    if tipo == "manual":
        if config.get("primeira_competencia_regra") == "manual":
            return None
        return add_months_month_start(cronograma.primeira_cobranca_valida, offset)

    if tipo == "contemplacao":
        return month_start(contemplacao_data) if contemplacao_data else None

    return None


def get_contemplacao_date_for_cota(supa: Client, org_id: str, cota_id: str, contrato: Dict[str, Any]) -> Optional[date]:
    contrato_data = parse_iso_date(contrato.get("data_contemplacao"))
    if contrato_data:
        return contrato_data

    resp = (
        supa.table("contemplacoes")
        .select("data")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .order("data", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        return None
    return parse_iso_date(rows[0]["data"])


def infer_status(competencia_prevista: Optional[date], tipo_evento: str, contemplacao_data: Optional[date]) -> str:
    today = month_start(date.today())
    if tipo_evento == "contemplacao":
        return "disponivel" if contemplacao_data else "previsto"
    if competencia_prevista and competencia_prevista <= today:
        return "disponivel"
    return "previsto"


def build_launches_payload(
    *,
    supa: Client,
    org_id: str,
    contrato: Dict[str, Any],
    cota: Dict[str, Any],
    config: Dict[str, Any],
    regras: List[Dict[str, Any]],
    parceiros: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    valor_carta = _dec(cota.get("valor_carta"))
    if valor_carta <= 0:
        raise HTTPException(400, "valor_carta da cota precisa ser maior que zero")

    cronograma = compute_cronograma_base(cota, config)
    contemplacao_data = get_contemplacao_date_for_cota(supa, org_id, cota["id"], contrato)
    now_iso = datetime.utcnow().isoformat()

    launches: List[Dict[str, Any]] = []
    total_pct = _dec(config["percentual_total"])
    if total_pct <= 0:
        raise HTTPException(400, "percentual_total da comissão precisa ser maior que zero")

    for regra in regras:
        regra_pct = _dec(regra["percentual_comissao"])
        competencia_prevista = determine_competencia_prevista(regra, cronograma, config, contemplacao_data)
        status = infer_status(competencia_prevista, regra["tipo_evento"], contemplacao_data)
        liberado_por_evento_em = now_iso if (regra["tipo_evento"] == "contemplacao" and contemplacao_data) else None

        valor_bruto_empresa = _money(valor_carta * (regra_pct / Decimal("100")))
        launches.append(
            {
                "org_id": org_id,
                "contrato_id": contrato["id"],
                "cota_id": cota["id"],
                "cota_comissao_config_id": config["id"],
                "regra_id": regra["id"],
                "parceiro_id": None,
                "beneficiario_tipo": "empresa",
                "tipo_evento": regra["tipo_evento"],
                "ordem": int(regra["ordem"]),
                "competencia_prevista": competencia_prevista.isoformat() if competencia_prevista else None,
                "competencia_real": None,
                "percentual_base": str(_pct(regra_pct)),
                "valor_base": str(_money(valor_carta)),
                "valor_bruto": str(valor_bruto_empresa),
                "imposto_pct": "0.0000",
                "valor_imposto": "0.00",
                "valor_liquido": str(valor_bruto_empresa),
                "status": status,
                "liberado_por_evento_em": liberado_por_evento_em,
                "repasse_status": "nao_aplicavel",
            }
        )

        for parceiro in parceiros:
            parceiro_pct = _dec(parceiro["percentual_parceiro"])
            ratio = parceiro_pct / total_pct
            parceiro_pct_regra = _pct(regra_pct * ratio)
            valor_bruto = _money(valor_carta * (parceiro_pct_regra / Decimal("100")))
            imposto_pct = _dec(parceiro.get("imposto_retido_pct"))
            valor_imposto = _money(valor_bruto * (imposto_pct / Decimal("100")))
            valor_liquido = _money(valor_bruto - valor_imposto)

            launches.append(
                {
                    "org_id": org_id,
                    "contrato_id": contrato["id"],
                    "cota_id": cota["id"],
                    "cota_comissao_config_id": config["id"],
                    "regra_id": regra["id"],
                    "parceiro_id": parceiro["parceiro_id"],
                    "beneficiario_tipo": "parceiro",
                    "tipo_evento": regra["tipo_evento"],
                    "ordem": int(regra["ordem"]),
                    "competencia_prevista": competencia_prevista.isoformat() if competencia_prevista else None,
                    "competencia_real": None,
                    "percentual_base": str(parceiro_pct_regra),
                    "valor_base": str(_money(valor_carta)),
                    "valor_bruto": str(valor_bruto),
                    "imposto_pct": str(_pct(imposto_pct)),
                    "valor_imposto": str(valor_imposto),
                    "valor_liquido": str(valor_liquido),
                    "status": status,
                    "liberado_por_evento_em": liberado_por_evento_em,
                    "repasse_status": "pendente",
                    "repasse_previsto_em": competencia_prevista.isoformat() if competencia_prevista else None,
                }
            )

    return launches


def fetch_lancamentos(supa: Client, org_id: str, **filters: Any) -> List[Dict[str, Any]]:
    query = (
        supa.table("comissao_lancamentos")
        .select("*, parceiros_corretores(id, nome)")
        .eq("org_id", org_id)
    )

    for key, value in filters.items():
        if value is None:
            continue
        if key == "competencia_de":
            query = query.gte("competencia_prevista", value.isoformat())
        elif key == "competencia_ate":
            query = query.lte("competencia_prevista", value.isoformat())
        else:
            query = query.eq(key, value)

    resp = query.order("competencia_prevista").order("ordem").execute()
    return getattr(resp, "data", None) or []


def summarize_lancamentos(lancamentos: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {
        "total_lancamentos": len(lancamentos),
        "total_bruto_empresa": Decimal("0"),
        "total_bruto_parceiros": Decimal("0"),
        "total_liquido_parceiros": Decimal("0"),
        "total_impostos_parceiros": Decimal("0"),
        "repasses_pendentes": 0,
        "repasses_pagos": 0,
    }

    for item in lancamentos:
        bruto = _dec(item.get("valor_bruto"))
        liquido = _dec(item.get("valor_liquido"))
        imposto = _dec(item.get("valor_imposto"))

        if item["beneficiario_tipo"] == "empresa":
            summary["total_bruto_empresa"] += bruto
        else:
            summary["total_bruto_parceiros"] += bruto
            summary["total_liquido_parceiros"] += liquido
            summary["total_impostos_parceiros"] += imposto
            if item.get("repasse_status") == "pendente":
                summary["repasses_pendentes"] += 1
            elif item.get("repasse_status") == "pago":
                summary["repasses_pagos"] += 1

    return {
        **summary,
        "total_bruto_empresa": str(_money(summary["total_bruto_empresa"])),
        "total_bruto_parceiros": str(_money(summary["total_bruto_parceiros"])),
        "total_liquido_parceiros": str(_money(summary["total_liquido_parceiros"])),
        "total_impostos_parceiros": str(_money(summary["total_impostos_parceiros"])),
    }


def upsert_config_for_cota(supa: Client, org_id: str, cota_id: str, payload: CotaComissaoConfigUpsertIn) -> Dict[str, Any]:
    cota = fetch_cota_context(supa, org_id, cota_id)
    validate_partner_ids(supa, org_id, payload)

    existing = fetch_config_by_cota(supa, org_id, cota_id)
    config_payload = {
        "org_id": org_id,
        "cota_id": cota_id,
        "percentual_total": str(_pct(payload.percentual_total)),
        "base_calculo": payload.base_calculo,
        "modo": payload.modo,
        "imposto_padrao_pct": str(_pct(payload.imposto_padrao_pct)),
        "primeira_competencia_regra": payload.primeira_competencia_regra,
        "furo_meses_override": payload.furo_meses_override,
        "ativo": payload.ativo,
        "observacoes": payload.observacoes,
        "updated_at": datetime.utcnow().isoformat(),
    }

    if existing:
        resp = (
            supa.table("cota_comissao_config")
            .update(config_payload)
            .eq("id", existing["id"])
            .eq("org_id", org_id)
            .execute()
        )
        config_id = existing["id"]
    else:
        config_payload["created_at"] = datetime.utcnow().isoformat()
        resp = (
            supa.table("cota_comissao_config")
            .insert(config_payload, returning="representation")
            .execute()
        )
        data = getattr(resp, "data", None) or []
        if not data:
            raise HTTPException(500, "Erro ao criar configuração de comissão")
        config_id = data[0]["id"]

    supa.table("cota_comissao_regras").delete().eq("org_id", org_id).eq("cota_comissao_config_id", config_id).execute()
    supa.table("cota_comissao_parceiros").delete().eq("org_id", org_id).eq("cota_id", cota_id).execute()

    regras_rows = [
        {
            "org_id": org_id,
            "cota_comissao_config_id": config_id,
            "ordem": item.ordem,
            "tipo_evento": item.tipo_evento,
            "offset_meses": item.offset_meses,
            "percentual_comissao": str(_pct(item.percentual_comissao)),
            "descricao": item.descricao,
        }
        for item in sorted(payload.regras, key=lambda r: r.ordem)
    ]
    supa.table("cota_comissao_regras").insert(regras_rows).execute()

    if payload.parceiros:
        parceiros_rows = [
            {
                "org_id": org_id,
                "cota_id": cota_id,
                "parceiro_id": item.parceiro_id,
                "percentual_parceiro": str(_pct(item.percentual_parceiro)),
                "imposto_retido_pct": str(_pct(item.imposto_retido_pct)),
                "ativo": item.ativo,
                "observacoes": item.observacoes,
                "updated_at": datetime.utcnow().isoformat(),
            }
            for item in payload.parceiros
        ]
        supa.table("cota_comissao_parceiros").insert(parceiros_rows).execute()

    config = fetch_config_by_cota(supa, org_id, cota_id)
    regras = fetch_regras(supa, org_id, config_id)
    parceiros = fetch_parceiros_da_cota(supa, org_id, cota_id)

    return {
        "ok": True,
        "cota": cota,
        "config": config,
        "regras": regras,
        "parceiros": parceiros,
    }


def generate_lancamentos_for_contrato(supa: Client, org_id: str, contrato_id: str, sobrescrever: bool = False) -> Dict[str, Any]:
    contrato = fetch_contrato_context(supa, org_id, contrato_id)
    cota = fetch_cota_context(supa, org_id, contrato["cota_id"])
    config = fetch_config_by_cota(supa, org_id, cota["id"])
    if not config:
        raise HTTPException(404, "A cota deste contrato não possui configuração de comissão")

    regras = fetch_regras(supa, org_id, config["id"])
    parceiros = fetch_parceiros_da_cota(supa, org_id, cota["id"])

    existing_resp = (
        supa.table("comissao_lancamentos")
        .select("id")
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .limit(1)
        .execute()
    )
    existing = getattr(existing_resp, "data", None) or []
    if existing and not sobrescrever:
        raise HTTPException(409, "Já existem lançamentos para este contrato. Use sobrescrever=true para recriar")

    if existing and sobrescrever:
        supa.table("comissao_lancamentos").delete().eq("org_id", org_id).eq("contrato_id", contrato_id).execute()

    launches = build_launches_payload(
        supa=supa,
        org_id=org_id,
        contrato=contrato,
        cota=cota,
        config=config,
        regras=regras,
        parceiros=parceiros,
    )

    created = supa.table("comissao_lancamentos").insert(launches, returning="representation").execute()
    data = getattr(created, "data", None) or []
    return {
        "ok": True,
        "contrato": contrato,
        "cota": cota,
        "config": config,
        "gerados": len(data),
        "lancamentos": data,
        "resumo": summarize_lancamentos(data),
    }


def sync_eventos_contrato(supa: Client, org_id: str, contrato_id: str) -> Dict[str, Any]:
    contrato = fetch_contrato_context(supa, org_id, contrato_id)
    contemplacao = get_contemplacao_date_for_cota(supa, org_id, contrato["cota_id"], contrato)
    if not contemplacao:
        return {"ok": True, "updated": 0, "detail": "Sem contemplação registrada"}

    payload = {
        "competencia_prevista": month_start(contemplacao).isoformat(),
        "status": "disponivel",
        "liberado_por_evento_em": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    updated = (
        supa.table("comissao_lancamentos")
        .update(payload)
        .eq("org_id", org_id)
        .eq("contrato_id", contrato_id)
        .eq("tipo_evento", "contemplacao")
        .neq("status", "pago")
        .execute()
    )
    rows = getattr(updated, "data", None) or []
    return {"ok": True, "updated": len(rows), "contemplacao_data": contemplacao.isoformat()}
