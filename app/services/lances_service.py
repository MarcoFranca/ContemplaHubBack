from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Optional

from fastapi import HTTPException
from supabase import Client

from app.security.auth import CurrentProfile


def normalize_competencia(dt: date) -> date:
    return date(dt.year, dt.month, 1)


def adjust_weekend(dt: date, tipo_ajuste: str) -> date:
    weekday = dt.weekday()  # seg=0, dom=6
    if weekday < 5:
        return dt

    if tipo_ajuste == "dia_util_anterior":
        if weekday == 5:  # sabado
            return dt - timedelta(days=1)
        return dt - timedelta(days=2)

    if tipo_ajuste == "proximo_dia_util":
        if weekday == 5:
            return dt + timedelta(days=2)
        return dt + timedelta(days=1)

    return dt


def build_assembleia_date(
    *,
    competencia: date,
    dia_base: int,
    ajustar_fim_semana: bool,
    tipo_ajuste: str,
) -> date:
    last_day = monthrange(competencia.year, competencia.month)[1]
    safe_day = min(dia_base, last_day)
    dt = date(competencia.year, competencia.month, safe_day)
    if ajustar_fim_semana:
        dt = adjust_weekend(dt, tipo_ajuste)
    return dt


def get_cota_or_404(*, sb: Client, org_id: str, cota_id: str) -> dict[str, Any]:
    resp = (
        sb.table("cotas")
        .select("""
            id,
            org_id,
            lead_id,
            administradora_id,
            numero_cota,
            grupo_codigo,
            produto,
            valor_carta,
            valor_parcela,
            prazo,
            embutido_permitido,
            embutido_max_percent,
            fgts_permitido,
            autorizacao_gestao,
            tipo_lance_preferencial,
            data_ultimo_lance,
            objetivo,
            estrategia,
            assembleia_dia,
            status,
            created_at
        """)
        .eq("org_id", org_id)
        .eq("id", cota_id)
        .single()
        .execute()
    )
    data = getattr(resp, "data", None)
    if not data:
        raise HTTPException(404, "Cota não encontrada")
    return data


def get_latest_diagnostico(*, sb: Client, org_id: str, lead_id: str | None) -> Optional[dict[str, Any]]:
    if not lead_id:
        return None

    resp = (
        sb.table("lead_diagnosticos")
        .select("""
            id,
            lead_id,
            estrategia_lance,
            lance_base_pct,
            lance_max_pct,
            readiness_score,
            created_at
        """)
        .eq("org_id", org_id)
        .eq("lead_id", lead_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def get_controle_mensal(
    *,
    sb: Client,
    org_id: str,
    cota_id: str,
    competencia: date,
) -> Optional[dict[str, Any]]:
    resp = (
        sb.table("cota_lance_competencias")
        .select("*")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .eq("competencia", competencia.isoformat())
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def get_regra_assembleia(
    *,
    sb: Client,
    org_id: str,
    administradora_id: str | None,
    produto: str | None,
) -> Optional[dict[str, Any]]:
    if not administradora_id:
        return None

    # 1) regra específica por produto
    if produto:
        resp_prod = (
            sb.table("administradora_regras_lance")
            .select("*")
            .eq("org_id", org_id)
            .eq("administradora_id", administradora_id)
            .eq("produto", produto)
            .limit(1)
            .execute()
        )
        rows_prod = getattr(resp_prod, "data", None) or []
        if rows_prod:
            return rows_prod[0]

    # 2) regra genérica
    resp_generic = (
        sb.table("administradora_regras_lance")
        .select("*")
        .eq("org_id", org_id)
        .eq("administradora_id", administradora_id)
        .is_("produto", "null")
        .limit(1)
        .execute()
    )
    rows_generic = getattr(resp_generic, "data", None) or []
    return rows_generic[0] if rows_generic else None


def resolve_assembleia(
    *,
    sb: Client,
    org_id: str,
    cota: dict[str, Any],
    competencia: date,
) -> dict[str, Any]:
    competencia = normalize_competencia(competencia)

    if cota.get("assembleia_dia"):
        assembleia_prevista = build_assembleia_date(
            competencia=competencia,
            dia_base=int(cota["assembleia_dia"]),
            ajustar_fim_semana=True,
            tipo_ajuste="proximo_dia_util",
        )
        return {
            "origem": "cota",
            "produto": cota.get("produto"),
            "dia_base_assembleia": cota.get("assembleia_dia"),
            "ajustar_fim_semana": True,
            "tipo_ajuste": "proximo_dia_util",
            "assembleia_prevista": assembleia_prevista,
        }

    regra = get_regra_assembleia(
        sb=sb,
        org_id=org_id,
        administradora_id=cota.get("administradora_id"),
        produto=cota.get("produto"),
    )
    if regra:
        assembleia_prevista = build_assembleia_date(
            competencia=competencia,
            dia_base=int(regra["dia_base_assembleia"]),
            ajustar_fim_semana=bool(regra.get("ajustar_fim_semana", True)),
            tipo_ajuste=regra.get("tipo_ajuste") or "proximo_dia_util",
        )
        return {
            "origem": "regra_operadora",
            "produto": regra.get("produto"),
            "dia_base_assembleia": regra.get("dia_base_assembleia"),
            "ajustar_fim_semana": regra.get("ajustar_fim_semana"),
            "tipo_ajuste": regra.get("tipo_ajuste"),
            "assembleia_prevista": assembleia_prevista,
        }

    return {
        "origem": None,
        "produto": cota.get("produto"),
        "dia_base_assembleia": None,
        "ajustar_fim_semana": None,
        "tipo_ajuste": None,
        "assembleia_prevista": None,
    }


def get_ultimo_lance(*, sb: Client, org_id: str, cota_id: str) -> Optional[dict[str, Any]]:
    resp = (
        sb.table("lances")
        .select("id, cota_id, assembleia_data, tipo, percentual, valor, origem, resultado, created_at")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .order("assembleia_data", desc=True)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def get_historico_lances(*, sb: Client, org_id: str, cota_id: str) -> list[dict[str, Any]]:
    resp = (
        sb.table("lances")
        .select("*")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .order("assembleia_data", desc=True)
        .order("created_at", desc=True)
        .execute()
    )
    return getattr(resp, "data", None) or []


def get_contemplacao(*, sb: Client, org_id: str, cota_id: str) -> Optional[dict[str, Any]]:
    resp = (
        sb.table("contemplacoes")
        .select("*")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def ensure_cota_ativa(cota: dict[str, Any]) -> None:
    if cota.get("status") != "ativa":
        raise HTTPException(400, "Apenas cotas ativas podem receber operação de lance")


def upsert_controle_mensal(
    *,
    sb: Client,
    profile: CurrentProfile,
    cota_id: str,
    competencia: date,
    status_mes: str,
    observacoes: str | None = None,
    assembleia_prevista: date | None = None,
    lance_id: str | None = None,
) -> dict[str, Any]:
    competencia = normalize_competencia(competencia)
    existing = get_controle_mensal(
        sb=sb,
        org_id=profile.org_id,
        cota_id=cota_id,
        competencia=competencia,
    )

    payload = {
        "org_id": profile.org_id,
        "cota_id": cota_id,
        "competencia": competencia.isoformat(),
        "assembleia_prevista": assembleia_prevista.isoformat() if assembleia_prevista else None,
        "status_mes": status_mes,
        "lance_id": lance_id,
        "observacoes": observacoes,
    }

    if existing:
        resp = (
            sb.table("cota_lance_competencias")
            .update(payload)
            .eq("id", existing["id"])
            .eq("org_id", profile.org_id)
            .execute()
        )
    else:
        resp = (
            sb.table("cota_lance_competencias")
            .insert(payload, returning="representation")
            .execute()
        )

    rows = getattr(resp, "data", None) or []
    if rows:
        return rows[0]

    return existing or payload


def list_cartas_operacao(
    *,
    sb: Client,
    profile: CurrentProfile,
    competencia: date,
    status_cota: str = "ativa",
    administradora_id: str | None = None,
    produto: str | None = None,
    somente_autorizadas: bool = False,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    competencia = normalize_competencia(competencia)

    query = (
        sb.table("cotas")
        .select("""
            id,
            org_id,
            lead_id,
            administradora_id,
            numero_cota,
            grupo_codigo,
            produto,
            valor_carta,
            valor_parcela,
            prazo,
            embutido_permitido,
            embutido_max_percent,
            fgts_permitido,
            autorizacao_gestao,
            tipo_lance_preferencial,
            data_ultimo_lance,
            objetivo,
            estrategia,
            assembleia_dia,
            status,
            created_at,
            leads ( id, nome ),
            administradoras ( id, nome )
        """, count="exact")
        .eq("org_id", profile.org_id)
    )

    if status_cota != "all":
        query = query.eq("status", status_cota)

    if administradora_id:
        query = query.eq("administradora_id", administradora_id)

    if produto:
        query = query.eq("produto", produto)

    if somente_autorizadas:
        query = query.eq("autorizacao_gestao", True)

    # busca simples pelo número da cota ou grupo
    if q:
        query = query.or_(f"numero_cota.ilike.%{q}%,grupo_codigo.ilike.%{q}%")

    start = (page - 1) * page_size
    end = start + page_size - 1

    resp = query.order("created_at", desc=True).range(start, end).execute()
    rows = getattr(resp, "data", None) or []
    total = getattr(resp, "count", None) or len(rows)

    items: list[dict[str, Any]] = []
    for cota in rows:
        cota_id = cota["id"]
        controle = get_controle_mensal(
            sb=sb,
            org_id=profile.org_id,
            cota_id=cota_id,
            competencia=competencia,
        )
        regra = resolve_assembleia(
            sb=sb,
            org_id=profile.org_id,
            cota=cota,
            competencia=competencia,
        )

        items.append({
            "cota_id": cota_id,
            "lead_id": cota.get("lead_id"),
            "cliente_nome": (cota.get("leads") or {}).get("nome") if cota.get("leads") else None,
            "administradora_id": cota.get("administradora_id"),
            "administradora_nome": (cota.get("administradoras") or {}).get("nome") if cota.get("administradoras") else None,
            "produto": cota["produto"],
            "grupo_codigo": cota["grupo_codigo"],
            "numero_cota": cota["numero_cota"],
            "valor_carta": cota.get("valor_carta"),
            "valor_parcela": cota.get("valor_parcela"),
            "prazo": cota.get("prazo"),
            "status": cota.get("status"),
            "autorizacao_gestao": bool(cota.get("autorizacao_gestao")),
            "embutido_permitido": bool(cota.get("embutido_permitido")),
            "embutido_max_percent": cota.get("embutido_max_percent"),
            "fgts_permitido": bool(cota.get("fgts_permitido")),
            "tipo_lance_preferencial": cota.get("tipo_lance_preferencial"),
            "estrategia": cota.get("estrategia"),
            "assembleia_dia_origem": regra.get("origem"),
            "assembleia_dia": regra.get("dia_base_assembleia"),
            "assembleia_prevista": regra.get("assembleia_prevista"),
            "competencia": competencia,
            "status_mes": (controle or {}).get("status_mes", "pendente"),
            "tem_pendencia_configuracao": regra.get("assembleia_prevista") is None,
        })

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def get_carta_detalhe(
    *,
    sb: Client,
    profile: CurrentProfile,
    cota_id: str,
    competencia: date,
) -> dict[str, Any]:
    competencia = normalize_competencia(competencia)
    cota = get_cota_or_404(sb=sb, org_id=profile.org_id, cota_id=cota_id)

    lead = None
    if cota.get("lead_id"):
        lead_resp = (
            sb.table("leads")
            .select("id, nome, telefone, email")
            .eq("org_id", profile.org_id)
            .eq("id", cota["lead_id"])
            .single()
            .execute()
        )
        lead = getattr(lead_resp, "data", None)

    administradora = None
    if cota.get("administradora_id"):
        adm_resp = (
            sb.table("administradoras")
            .select("id, nome")
            .eq("id", cota["administradora_id"])
            .limit(1)
            .execute()
        )
        adms = getattr(adm_resp, "data", None) or []
        administradora = adms[0] if adms else None

    regra = resolve_assembleia(
        sb=sb,
        org_id=profile.org_id,
        cota=cota,
        competencia=competencia,
    )
    controle = get_controle_mensal(
        sb=sb,
        org_id=profile.org_id,
        cota_id=cota_id,
        competencia=competencia,
    ) or {
        "id": None,
        "competencia": competencia.isoformat(),
        "status_mes": "pendente",
        "lance_id": None,
        "observacoes": None,
    }

    return {
        "cota": cota,
        "lead": lead,
        "administradora": administradora,
        "regra_assembleia": regra,
        "controle_mes_atual": controle,
        "historico_lances": get_historico_lances(sb=sb, org_id=profile.org_id, cota_id=cota_id),
        "contemplacao": get_contemplacao(sb=sb, org_id=profile.org_id, cota_id=cota_id),
        "diagnostico": get_latest_diagnostico(sb=sb, org_id=profile.org_id, lead_id=cota.get("lead_id")),
    }


def registrar_lance(
    *,
    sb: Client,
    profile: CurrentProfile,
    cota_id: str,
    competencia: date,
    assembleia_data: date,
    tipo: str,
    percentual: Any,
    valor: Any,
    base_calculo: str,
    pagamento: dict[str, Any] | None,
    resultado: str | None,
    observacoes_competencia: str | None,
) -> dict[str, Any]:
    cota = get_cota_or_404(sb=sb, org_id=profile.org_id, cota_id=cota_id)
    ensure_cota_ativa(cota)

    if tipo == "embutido" and not cota.get("embutido_permitido"):
        raise HTTPException(400, "Esta cota não permite lance embutido")

    if tipo == "embutido" and cota.get("embutido_max_percent") is not None and percentual is not None:
        if float(percentual) > float(cota["embutido_max_percent"]):
            raise HTTPException(400, "Percentual acima do limite de lance embutido da cota")

    if pagamento and pagamento.get("fonte") == "fgts" and not cota.get("fgts_permitido"):
        raise HTTPException(400, "Esta cota não permite FGTS para o lance")

    payload = {
        "org_id": profile.org_id,
        "cota_id": cota_id,
        "tipo": tipo,
        "percentual": percentual,
        "valor": valor,
        "origem": "executado",
        "created_by": profile.user_id,
        "assembleia_data": assembleia_data.isoformat(),
        "base_calculo": base_calculo,
        "pagamento": pagamento,
        "resultado": resultado or "pendente",
    }

    try:
        resp = sb.table("lances").insert(payload, returning="representation").execute()
    except Exception as e:
        raise HTTPException(409, f"Não foi possível registrar o lance: {str(e)}")

    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(500, "Falha ao registrar lance")
    lance = rows[0]

    (
        sb.table("cotas")
        .update({"data_ultimo_lance": assembleia_data.isoformat()})
        .eq("org_id", profile.org_id)
        .eq("id", cota_id)
        .execute()
    )

    controle = upsert_controle_mensal(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        competencia=competencia,
        status_mes="feito",
        observacoes=observacoes_competencia,
        assembleia_prevista=assembleia_data,
        lance_id=lance["id"],
    )

    return {"lance": lance, "controle_mes": controle}


def contemplar_cota(
    *,
    sb: Client,
    profile: CurrentProfile,
    cota_id: str,
    data: date,
    motivo: str,
    lance_percentual: Any,
    competencia: date,
) -> dict[str, Any]:
    cota = get_cota_or_404(sb=sb, org_id=profile.org_id, cota_id=cota_id)
    ensure_cota_ativa(cota)

    existing = get_contemplacao(sb=sb, org_id=profile.org_id, cota_id=cota_id)
    if existing:
        raise HTTPException(409, "Esta cota já possui contemplação registrada")

    resp = (
        sb.table("contemplacoes")
        .insert({
            "org_id": profile.org_id,
            "cota_id": cota_id,
            "motivo": motivo,
            "lance_percentual": lance_percentual,
            "data": data.isoformat(),
        }, returning="representation")
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(500, "Falha ao registrar contemplação")

    (
        sb.table("cotas")
        .update({"status": "contemplada"})
        .eq("org_id", profile.org_id)
        .eq("id", cota_id)
        .execute()
    )

    upsert_controle_mensal(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        competencia=competencia,
        status_mes="contemplada",
        observacoes="Cota contemplada",
        assembleia_prevista=data,
        lance_id=None,
    )

    return rows[0]


def cancelar_cota(
    *,
    sb: Client,
    profile: CurrentProfile,
    cota_id: str,
    competencia: date,
    observacoes: str | None,
) -> dict[str, Any]:
    cota = get_cota_or_404(sb=sb, org_id=profile.org_id, cota_id=cota_id)
    if cota.get("status") == "contemplada":
        raise HTTPException(400, "Não é permitido cancelar uma cota já contemplada")

    (
        sb.table("cotas")
        .update({"status": "cancelada"})
        .eq("org_id", profile.org_id)
        .eq("id", cota_id)
        .execute()
    )

    controle = upsert_controle_mensal(
        sb=sb,
        profile=profile,
        cota_id=cota_id,
        competencia=competencia,
        status_mes="cancelada",
        observacoes=observacoes,
        assembleia_prevista=None,
        lance_id=None,
    )

    return controle


def reativar_cota(
    *,
    sb: Client,
    profile: CurrentProfile,
    cota_id: str,
) -> dict[str, Any]:
    cota = get_cota_or_404(sb=sb, org_id=profile.org_id, cota_id=cota_id)
    if cota.get("status") != "cancelada":
        raise HTTPException(400, "Apenas cotas canceladas podem ser reativadas")

    resp = (
        sb.table("cotas")
        .update({"status": "ativa"})
        .eq("org_id", profile.org_id)
        .eq("id", cota_id)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else {"ok": True}


def list_regras_operadora(*, sb: Client, profile: CurrentProfile) -> list[dict[str, Any]]:
    resp = (
        sb.table("administradora_regras_lance")
        .select("""
            id,
            org_id,
            administradora_id,
            produto,
            dia_base_assembleia,
            ajustar_fim_semana,
            tipo_ajuste,
            observacoes,
            created_at
        """)
        .eq("org_id", profile.org_id)
        .order("created_at", desc=False)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    items: list[dict[str, Any]] = []

    for row in rows:
        adm_resp = (
            sb.table("administradoras")
            .select("id, nome")
            .eq("id", row["administradora_id"])
            .limit(1)
            .execute()
        )
        adms = getattr(adm_resp, "data", None) or []
        items.append({
            **row,
            "administradora_nome": adms[0]["nome"] if adms else None,
        })

    return items