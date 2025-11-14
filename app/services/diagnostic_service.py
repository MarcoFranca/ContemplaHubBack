# app/services/diagnostic_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from supabase import Client

from app.schemas.diagnostic import (
    DiagnosticInput,
    DiagnosticScores,
    DiagnosticRecord,
    DiagnosticResponse,
)


def _compute_scores(input: DiagnosticInput) -> DiagnosticScores:
    """
    MVP de motor de scoring.
    Depois podemos trocar por modelo preditivo real,
    mas a interface de saída se mantém.
    """

    # Base = 40
    readiness = 40
    risco = 60

    # 1) Capacidade de pagar a parcela
    if (
        input.renda_mensal is not None
        and input.comprometimento_max_pct is not None
        and input.valor_carta_alvo is not None
        and input.prazo_alvo_meses
    ):
        # capacidade máxima de parcela
        max_parcela = input.renda_mensal * (input.comprometimento_max_pct / 100.0)
        parcela_teorica = input.valor_carta_alvo / max(input.prazo_alvo_meses, 1)

        if parcela_teorica > 0:
            ratio = max_parcela / parcela_teorica
        else:
            ratio = 0

        if ratio >= 1.5:
            readiness += 30
            risco -= 20
        elif ratio >= 1.0:
            readiness += 20
            risco -= 10
        elif ratio >= 0.7:
            readiness += 10
            risco -= 5
        else:
            readiness += 0
            risco += 5

    # 2) Reserva inicial
    if input.reserva_inicial is not None and input.valor_carta_alvo:
        pct_reserva = input.reserva_inicial / max(input.valor_carta_alvo, 1)
        if pct_reserva >= 0.2:
            readiness += 15
            risco -= 10
        elif pct_reserva >= 0.1:
            readiness += 8
            risco -= 5

    # 3) Renda provada dá um pequeno boost
    if input.renda_provada:
        readiness += 5
        risco -= 5

    # clamp 0–100
    readiness = max(0, min(100, int(round(readiness))))
    risco = max(0, min(100, int(round(risco))))

    # Probabilidades derivadas de forma simples
    # (depois o modelo de IA pode sobrescrever isso)
    prob_conversao = round(readiness / 100 * 0.85, 3)

    # Distribuir probabilidade de contemplação em janelas
    # (short = 0–12m, med = 12–36m, long >36m – conceitual)
    base = readiness / 100
    prob_short = round(base * 0.4, 3)
    prob_med = round(base * 0.35, 3)
    prob_long = round(base * 0.25, 3)

    return DiagnosticScores(
        score_risco=risco,
        readiness_score=readiness,
        prob_conversao=prob_conversao,
        prob_contemplacao_short=prob_short,
        prob_contemplacao_med=prob_med,
        prob_contemplacao_long=prob_long,
    )


def save_diagnostic(
    org_id: str,
    lead_id: str,
    input: DiagnosticInput,
    supa: Client,
) -> DiagnosticResponse:
    """
    Salva (insert ou update) diagnóstico na tabela lead_diagnosticos
    para o par (org_id, lead_id).

    NÃO usa upsert com on_conflict, pois a tabela não tem constraint única
    em (org_id, lead_id). Fazemos o "upsert manual":
      - se existe, damos update
      - se não existe, damos insert
    """

    scores = _compute_scores(input)
    now = datetime.now(timezone.utc)

    base_row = {
        "org_id": org_id,
        "lead_id": lead_id,
        # Objetivo & contexto
        "objetivo": input.objetivo,
        "prazo_meta_meses": input.prazo_meta_meses,
        "preferencia_produto": input.preferencia_produto,
        "regiao_preferencia": input.regiao_preferencia,
        # Capacidade financeira
        "renda_mensal": input.renda_mensal,
        "reserva_inicial": input.reserva_inicial,
        "comprometimento_max_pct": input.comprometimento_max_pct,
        "renda_provada": input.renda_provada,
        # Carta alvo
        "valor_carta_alvo": input.valor_carta_alvo,
        "prazo_alvo_meses": input.prazo_alvo_meses,
        # Estratégia de lance
        "estrategia_lance": input.estrategia_lance,
        "lance_base_pct": input.lance_base_pct,
        "lance_max_pct": input.lance_max_pct,
        "janela_preferida_semanas": input.janela_preferida_semanas,
        # Scores
        "score_risco": scores.score_risco,
        "readiness_score": scores.readiness_score,
        "prob_conversao": scores.prob_conversao,
        "prob_contemplacao_short": scores.prob_contemplacao_short,
        "prob_contemplacao_med": scores.prob_contemplacao_med,
        "prob_contemplacao_long": scores.prob_contemplacao_long,
        # LGPD & extras
        "consent_scope": input.consent_scope,
        "consent_ts": now.isoformat() if input.consent_scope else None,
        "extras": input.extras or {},
        # updated_at
        "updated_at": now.isoformat(),
    }

    # 1) Verifica se já existe registro para (org_id, lead_id)
    existing_resp = (
        supa.table("lead_diagnosticos")
        .select("id")
        .eq("org_id", org_id)
        .eq("lead_id", lead_id)
        .limit(1)
        .execute()
    )

    existing_rows = getattr(existing_resp, "data", None) or []
    exists = bool(existing_rows)

    if exists:
        # 2A) UPDATE
        saved_resp = (
            supa.table("lead_diagnosticos")
            .update(base_row)
            .eq("org_id", org_id)
            .eq("lead_id", lead_id)
            .execute()
        )
    else:
        # 2B) INSERT (incluímos created_at)
        insert_row = {
            **base_row,
            "created_at": now.isoformat(),
        }
        saved_resp = supa.table("lead_diagnosticos").insert(insert_row).execute()

    saved_rows = getattr(saved_resp, "data", None) or []

    # Se por algum motivo não veio linha de volta, usamos o base_row/insert_row
    saved = saved_rows[0] if saved_rows else {**base_row, "org_id": org_id, "lead_id": lead_id}

    record = DiagnosticRecord(
        id=saved.get("id"),
        org_id=saved.get("org_id", org_id),
        lead_id=saved.get("lead_id", lead_id),
        objetivo=saved.get("objetivo"),
        prazo_meta_meses=saved.get("prazo_meta_meses"),
        preferencia_produto=saved.get("preferencia_produto"),
        regiao_preferencia=saved.get("regiao_preferencia"),
        renda_mensal=saved.get("renda_mensal"),
        reserva_inicial=saved.get("reserva_inicial"),
        comprometimento_max_pct=saved.get("comprometimento_max_pct"),
        renda_provada=saved.get("renda_provada"),
        valor_carta_alvo=saved.get("valor_carta_alvo"),
        prazo_alvo_meses=saved.get("prazo_alvo_meses"),
        estrategia_lance=saved.get("estrategia_lance"),
        lance_base_pct=saved.get("lance_base_pct"),
        lance_max_pct=saved.get("lance_max_pct"),
        janela_preferida_semanas=saved.get("janela_preferida_semanas"),
        score_risco=saved.get("score_risco"),
        readiness_score=saved.get("readiness_score"),
        prob_conversao=saved.get("prob_conversao"),
        prob_contemplacao_short=saved.get("prob_contemplacao_short"),
        prob_contemplacao_med=saved.get("prob_contemplacao_med"),
        prob_contemplacao_long=saved.get("prob_contemplacao_long"),
        consent_scope=saved.get("consent_scope"),
        consent_ts=saved.get("consent_ts"),
        extras=saved.get("extras"),
        created_at=saved.get("created_at"),
        updated_at=saved.get("updated_at"),
    )

    return DiagnosticResponse(
        lead_id=lead_id,
        org_id=org_id,
        scores=scores,
        record=record,
    )


def get_diagnostic(
    org_id: str,
    lead_id: str,
    supa: Client,
) -> Optional[DiagnosticRecord]:
    """
    Busca o diagnóstico existente de um lead, se houver.
    Usa select + limit(1) em vez de maybe_single() para evitar
    problemas de None / diferenças de versão do client.
    """
    resp = (
        supa.table("lead_diagnosticos")
        .select("*")
        .eq("org_id", org_id)
        .eq("lead_id", lead_id)
        .limit(1)
        .execute()
    )

    # resp pode ser None em alguns cenários, então protegemos:
    if resp is None:
        return None

    rows = getattr(resp, "data", None) or []
    if not rows:
        return None

    row = rows[0]

    # Construir o DiagnosticRecord a partir da linha
    return DiagnosticRecord(**row)
