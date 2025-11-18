# app/services/kanban_interest_insights.py
from __future__ import annotations

from typing import Any, List, Optional

from app.schemas.kanban import Interest, InterestInsight


def _score_interest(i: Interest) -> int:
    s = 0
    if i.produto:
        s += 20
    if i.prazoMeses and i.prazoMeses >= 60:
        s += 15

    # tenta converter valorTotal para número
    v: Optional[float] = None
    if i.valorTotal:
        # mantém simples: pega só dígitos
        digits = "".join(ch for ch in i.valorTotal if ch.isdigit())
        if digits:
            v = float(digits)

    if v is not None:
        if v >= 200_000:
            s += 25
        if v >= 500_000:
            s += 10

    if i.objetivo:
        s += 10
    if i.perfilDesejado:
        s += 10
    if i.observacao:
        s += 10

    return min(100, s)


def _missing_fields(i: Interest) -> List[str]:
    miss: List[str] = []
    if not i.produto:
        miss.append("Produto")
    if not i.prazoMeses:
        miss.append("Prazo")
    if not i.valorTotal:
        miss.append("Valor da carta")
    if not i.objetivo:
        miss.append("Objetivo")
    if not i.perfilDesejado:
        miss.append("Perfil desejado")
    return miss


def _next_best_action(i: Interest, readiness_score: Optional[int]) -> str:
    # simples, inspirado no front, mas já abrindo espaço pro diagnóstico
    prod = i.produto or ""
    v = 0.0
    if i.valorTotal:
        digits = "".join(ch for ch in i.valorTotal if ch.isdigit())
        if digits:
            v = float(digits)

    # leitura básica de prontidão
    pronto = readiness_score is not None and readiness_score >= 70

    if prod == "imobiliario":
        if v >= 300_000 and pronto:
            return (
                "Priorize agendar uma reunião de proposta para estruturar carta entre "
                "180–200 meses, revisar capacidade de parcela e calibrar lances."
            )
        else:
            return (
                "Marque uma conversa rápida para alinhar objetivo (moradia, renda, "
                "segunda moradia) e confirmar valor/prazo antes de apresentar propostas."
            )

    if prod == "auto":
        return (
            "Confirme uso principal (trabalho, família, app) e modelo/ano pretendido. "
            "Depois, apresente 1–2 simulações com prazos diferentes para mostrar "
            "equilíbrio entre parcela e tempo de contemplação."
        )

    # fallback genérico
    return (
        "Use a próxima interação para esclarecer produto, objetivo e prazo. "
        "Com isso alinhado, avance para o diagnóstico completo e simulações."
    )


def _suggested_questions(i: Interest) -> List[str]:
    base: List[str] = [
        "Hoje qual é a principal prioridade financeira da família?",
        "Quanto tempo você imagina até usar essa carta com conforto?",
        "Qual valor de parcela encaixa no orçamento sem apertar?",
        "Você já teve experiência anterior com consórcio ou financiamento?",
        "Tem alguma preocupação específica com consórcio (prazo, lance, parcelas)?",
    ]
    if i.produto == "imobiliario":
        base.insert(1, "O imóvel é para moradia própria, renda (aluguel/Airbnb) ou segunda moradia?")
    if i.produto == "auto":
        base.insert(1, "O carro será mais para trabalho, família ou aplicativo? Já tem modelo em mente?")
    return base


def _likely_objections(i: Interest) -> List[str]:
    return [
        "Valor da parcela em relação ao orçamento mensal.",
        "Prazo percebido como longo demais.",
        "Ansiedade pela contemplação (tempo x lance).",
        "Comparação com financiamento tradicional (juros vs. disciplina do consórcio).",
    ]


def build_interest_insight(
    interest: Optional[Interest],
    diag: Optional[dict[str, Any]],
) -> Optional[InterestInsight]:
    if not interest:
        return None

    score = _score_interest(interest)
    missing = _missing_fields(interest)
    readiness = diag.get("readiness_score") if diag else None

    nba = _next_best_action(interest, readiness)
    questions = _suggested_questions(interest)
    objections = _likely_objections(interest)

    # prioridade baseada em interesse + readiness
    if score >= 70 and (readiness is not None and readiness >= 70):
        priority = "alta"
    elif score >= 50:
        priority = "media"
    else:
        priority = "baixa"

    return InterestInsight(
        score=score,
        missing_fields=missing,
        next_best_action=nba,
        suggested_questions=questions,
        likely_objections=objections,
        priority=priority,
    )
