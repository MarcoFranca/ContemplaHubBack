# app/services/kanban_interest_insights.py
from __future__ import annotations

from typing import Any, List, Optional

from app.schemas.kanban import Interest, InterestInsight


def _parse_valor(i: Interest) -> float:
    if not i.valorTotal:
        return 0.0
    digits = "".join(ch for ch in i.valorTotal if ch.isdigit())
    return float(digits) if digits else 0.0


def _strategy_ideas(i: Interest, readiness: Optional[int]) -> list[str]:
    ideas: list[str] = []
    v = _parse_valor(i)
    pronto = readiness is not None and readiness >= 70

    # Sem valor definido ainda: foco em diagnóstico
    if v <= 0:
        ideas.append(
            "Usar próxima conversa para travar faixa de carta (ex.: 200 a 300 mil) "
            "e só então comparar administradoras / prazos."
        )
        return ideas

    # IMOBILIÁRIO – foco em redutor / combinação de cartas
    if i.produto == "imobiliario":
        if v >= 400_000:
            ideas.append(
                "Avaliar se faz sentido dividir em 2 cartas (ex.: 2×250 mil) para "
                "combinar moradia + renda futura (aluguel/Airbnb) e ter mais flexibilidade na contemplação."
            )
        if v >= 300_000 and pronto:
            ideas.append(
                "Simular carta com redutor no prazo máximo (ex.: 200–220m) para "
                "trazer parcela confortável e manter espaço para um eventual segundo investimento."
            )
        else:
            ideas.append(
                "Começar com uma carta única na faixa informada, comparando cenário com e sem redutor "
                "para mostrar impacto na parcela e na capacidade de lance."
            )

        if i.objetivo in ("primeira-casa", "moradia", "moradia-propria"):
            ideas.append(
                "Enfatizar segurança de longo prazo: carta voltada para moradia, "
                "com foco em não comprometer mais que 25–30% da renda familiar."
            )

    # AUTO – escadinha de valor
    elif i.produto == "auto":
        if v >= 80_000:
            ideas.append(
                "Testar simulação com carta um degrau acima do veículo alvo para "
                "dar margem a upgrades de modelo/ano sem sufocar o orçamento."
            )
        else:
            ideas.append(
                "Começar com carta alinhada ao modelo alvo e prazo entre 60–84 meses "
                "para equilibrar parcela e rapidez na contemplação."
            )

    # fallback genérico
    if not ideas:
        ideas.append(
            "Usar o interesse atual como ponto de partida e apresentar 2–3 cenários de carta "
            "(valor e prazo diferentes) para o cliente reagir e ajudar na escolha."
        )

    return ideas


def _suggested_ticket_splits(i: Interest) -> list[str]:
    splits: list[str] = []
    v = _parse_valor(i)
    if v <= 0:
        return splits

    # Divisões simples de ticket, só como sugestão visual
    if i.produto == "imobiliario" and v >= 400_000:
        splits.append("1× R$ {:.0f} mil (carta única)".format(v / 1000))
        splits.append("2× R$ {:.0f} mil (moradia + renda)".format((v / 2) / 1000))
    elif i.produto == "imobiliario":
        splits.append("1× R$ {:.0f} mil (carta principal)".format(v / 1000))

    if i.produto == "auto" and v >= 80_000:
        splits.append("1× carta alvo + 1× menor para upgrade futuro")

    return splits


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

    strategy_ideas = _strategy_ideas(interest, readiness)
    ticket_splits = _suggested_ticket_splits(interest)

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
        strategy_ideas=strategy_ideas or None,
        suggested_ticket_splits=ticket_splits or None,
    )

