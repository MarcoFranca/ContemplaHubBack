"""Motor de disponibilidade da agenda (estilo Calendly).

Gera os horários livres de uma agenda a partir de:
- regras semanais (agenda_regras) — faixas por dia da semana (0=domingo..6=sábado);
- bloqueios pontuais (agenda_bloqueios);
- agendamentos já existentes (agendamentos com status ativo).

Fuso: horário de Brasília fixo (UTC-03:00), coerente com o resto do agente.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from supabase import Client

logger = logging.getLogger(__name__)

_TZ = timezone(timedelta(hours=-3))  # America/Sao_Paulo (sem DST atual)
_STATUS_ATIVOS = ["agendado", "confirmado"]


# --------------------------------------------------------------------------- #
# Feriados nacionais (calculados, sem API externa). Bloqueiam o dia inteiro.
# --------------------------------------------------------------------------- #
def _pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Butcher)."""
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_nacionais(ano: int) -> dict[date, str]:
    """Feriados nacionais (fixos + móveis) do ano."""
    pascoa = _pascoa(ano)
    fer: dict[date, str] = {
        date(ano, 1, 1): "Confraternização Universal",
        date(ano, 4, 21): "Tiradentes",
        date(ano, 5, 1): "Dia do Trabalho",
        date(ano, 9, 7): "Independência do Brasil",
        date(ano, 10, 12): "Nossa Senhora Aparecida",
        date(ano, 11, 2): "Finados",
        date(ano, 11, 15): "Proclamação da República",
        date(ano, 11, 20): "Consciência Negra",
        date(ano, 12, 25): "Natal",
        pascoa - timedelta(days=48): "Carnaval (segunda)",
        pascoa - timedelta(days=47): "Carnaval",
        pascoa - timedelta(days=2): "Sexta-feira Santa",
        pascoa + timedelta(days=60): "Corpus Christi",
    }
    return fer


def _feriados_set(*, supa: Client, org_id: str, anos: set[int]) -> set[date]:
    """Dias bloqueados por feriado: nacionais (calculados) + custom da org."""
    dias: set[date] = set()
    for ano in anos:
        dias.update(feriados_nacionais(ano).keys())
    try:
        rows = getattr(
            supa.table("agenda_feriados").select("data").eq("org_id", org_id).execute(), "data", None
        ) or []
        for r in rows:
            try:
                dias.add(date.fromisoformat(r["data"]))
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return dias


def agora() -> datetime:
    return datetime.now(_TZ)


def resolver_calendario_para_lead(*, supa: Client, org_id: str, lead_id: Optional[str]) -> Optional[dict[str, Any]]:
    """Agenda do dono do lead; senão a primeira agenda ativa da org."""
    owner_id: Optional[str] = None
    if lead_id:
        try:
            resp = supa.table("leads").select("owner_id").eq("org_id", org_id).eq("id", lead_id).limit(1).execute()
            rows = getattr(resp, "data", None) or []
            if rows:
                owner_id = rows[0].get("owner_id")
        except Exception:  # noqa: BLE001
            pass

    try:
        q = supa.table("agenda_calendarios").select("*").eq("org_id", org_id).eq("ativo", True)
        if owner_id:
            r = q.eq("especialista_id", owner_id).limit(1).execute()
            rows = getattr(r, "data", None) or []
            if rows:
                return rows[0]
        r2 = (
            supa.table("agenda_calendarios")
            .select("*")
            .eq("org_id", org_id)
            .eq("ativo", True)
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        rows2 = getattr(r2, "data", None) or []
        return rows2[0] if rows2 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolver_calendario_falhou", extra={"org_id": org_id, "error": str(exc)})
        return None


def _parse_hora(v: str) -> tuple[int, int]:
    parts = (v or "0:0").split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def _overlaps(inicio: datetime, fim: datetime, ranges: list[tuple[datetime, datetime]]) -> bool:
    for r_ini, r_fim in ranges:
        if inicio < r_fim and fim > r_ini:
            return True
    return False


def _iso(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=_TZ)


def listar_slots(
    *,
    supa: Client,
    org_id: str,
    calendario: dict[str, Any],
    max_slots: int = 12,
) -> list[dict[str, Any]]:
    """Gera os próximos horários livres da agenda."""
    slot_min = int(calendario.get("slot_min") or 30)
    antecedencia = int(calendario.get("antecedencia_min") or 0)
    horizonte = int(calendario.get("horizonte_dias") or 14)
    cal_id = calendario.get("id")

    try:
        regras = getattr(
            supa.table("agenda_regras").select("weekday, hora_inicio, hora_fim").eq("calendario_id", cal_id).execute(),
            "data",
            None,
        ) or []
    except Exception:  # noqa: BLE001
        regras = []
    if not regras:
        return []

    limite = agora() + timedelta(days=horizonte)

    # ocupados: bloqueios + agendamentos ativos dentro do horizonte
    ocupados: list[tuple[datetime, datetime]] = []
    try:
        blqs = getattr(
            supa.table("agenda_bloqueios").select("inicio, fim").eq("calendario_id", cal_id).execute(), "data", None
        ) or []
        for b in blqs:
            ocupados.append((_iso(datetime.fromisoformat(b["inicio"])), _iso(datetime.fromisoformat(b["fim"]))))
    except Exception:  # noqa: BLE001
        pass
    try:
        ags = getattr(
            supa.table("agendamentos")
            .select("inicio, fim")
            .eq("org_id", org_id)
            .eq("calendario_id", cal_id)
            .in_("status", _STATUS_ATIVOS)
            .execute(),
            "data",
            None,
        ) or []
        for a in ags:
            ini = _iso(datetime.fromisoformat(a["inicio"]))
            fim = _iso(datetime.fromisoformat(a["fim"])) if a.get("fim") else ini + timedelta(minutes=slot_min)
            ocupados.append((ini, fim))
    except Exception:  # noqa: BLE001
        pass

    # regras por dia da semana (0=domingo..6=sábado, convenção JS)
    por_dow: dict[int, list[tuple[tuple[int, int], tuple[int, int]]]] = {}
    for r in regras:
        dow = int(r["weekday"])
        por_dow.setdefault(dow, []).append((_parse_hora(r["hora_inicio"]), _parse_hora(r["hora_fim"])))

    minimo = agora() + timedelta(minutes=antecedencia)
    slots: list[dict[str, Any]] = []
    dia = agora().replace(hour=0, minute=0, second=0, microsecond=0)
    limite_dia = (agora() + timedelta(days=horizonte)).date()
    feriados = _feriados_set(supa=supa, org_id=org_id, anos={agora().year, limite_dia.year})

    for _ in range(horizonte + 1):
        if dia.date() in feriados:
            dia = dia + timedelta(days=1)
            continue
        dow_js = (dia.weekday() + 1) % 7  # weekday() Mon=0 -> Sun=0
        for (h_ini, m_ini), (h_fim, m_fim) in por_dow.get(dow_js, []):
            cursor = dia.replace(hour=h_ini, minute=m_ini)
            fim_faixa = dia.replace(hour=h_fim, minute=m_fim)
            while cursor + timedelta(minutes=slot_min) <= fim_faixa:
                s_ini = cursor
                s_fim = cursor + timedelta(minutes=slot_min)
                if s_ini >= minimo and s_ini <= limite and not _overlaps(s_ini, s_fim, ocupados):
                    slots.append({"inicio": s_ini.isoformat(), "label": _label(s_ini)})
                    if len(slots) >= max_slots:
                        return slots
                cursor = s_fim
        dia = dia + timedelta(days=1)

    return slots


_DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _label(dt: datetime) -> str:
    return f"{_DIAS[dt.weekday()]} {dt.strftime('%d/%m')} às {dt.strftime('%H:%M')}"


def slot_disponivel(
    *, supa: Client, org_id: str, calendario: dict[str, Any], inicio: datetime, slot_min: Optional[int] = None
) -> bool:
    """Valida se um horário específico está dentro da disponibilidade e livre."""
    slot_min = int(slot_min or calendario.get("slot_min") or 30)
    inicio = _iso(inicio)
    fim = inicio + timedelta(minutes=slot_min)
    cal_id = calendario.get("id")

    # feriado bloqueia o dia inteiro
    if inicio.date() in _feriados_set(supa=supa, org_id=org_id, anos={inicio.year}):
        return False

    # dentro de alguma regra do dia?
    dow_js = (inicio.weekday() + 1) % 7
    try:
        regras = getattr(
            supa.table("agenda_regras").select("weekday, hora_inicio, hora_fim").eq("calendario_id", cal_id).eq("weekday", dow_js).execute(),
            "data",
            None,
        ) or []
    except Exception:  # noqa: BLE001
        regras = []
    dentro = False
    for r in regras:
        h_ini, m_ini = _parse_hora(r["hora_inicio"])
        h_fim, m_fim = _parse_hora(r["hora_fim"])
        faixa_ini = inicio.replace(hour=h_ini, minute=m_ini, second=0, microsecond=0)
        faixa_fim = inicio.replace(hour=h_fim, minute=m_fim, second=0, microsecond=0)
        if inicio >= faixa_ini and fim <= faixa_fim:
            dentro = True
            break
    if not dentro:
        return False

    # não conflita com bloqueio nem agendamento ativo?
    ocupados: list[tuple[datetime, datetime]] = []
    try:
        blqs = getattr(supa.table("agenda_bloqueios").select("inicio, fim").eq("calendario_id", cal_id).execute(), "data", None) or []
        for b in blqs:
            ocupados.append((_iso(datetime.fromisoformat(b["inicio"])), _iso(datetime.fromisoformat(b["fim"]))))
    except Exception:  # noqa: BLE001
        pass
    try:
        ags = getattr(
            supa.table("agendamentos").select("inicio, fim").eq("org_id", org_id).eq("calendario_id", cal_id).in_("status", _STATUS_ATIVOS).execute(),
            "data",
            None,
        ) or []
        for a in ags:
            ini = _iso(datetime.fromisoformat(a["inicio"]))
            f = _iso(datetime.fromisoformat(a["fim"])) if a.get("fim") else ini + timedelta(minutes=slot_min)
            ocupados.append((ini, f))
    except Exception:  # noqa: BLE001
        pass

    return not _overlaps(inicio, fim, ocupados)
