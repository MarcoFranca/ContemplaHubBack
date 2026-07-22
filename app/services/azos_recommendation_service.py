"""Recomendação explicável de coberturas Azos baseada em necessidade financeira.

Não substitui subscrição nem análise da seguradora. Os capitais são referências
limitadas pelas opções elegíveis devolvidas pela API Azos.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


def _norm(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", raw.lower()).strip()


def _capital(option: dict[str, Any], target: float) -> float:
    minimum = float(option.get("capital_minimo") or 1_000)
    maximum = float(option.get("capital_maximo") or target)
    multiple = float(option.get("multiplo") or 1_000)
    value = min(maximum, max(minimum, target))
    return float(max(minimum, math.floor(value / multiple) * multiple))


def _find(options: list[dict[str, Any]], *terms: str, exclude: tuple[str, ...] = ()) -> dict[str, Any] | None:
    for option in options:
        name = _norm(f"{option.get('nome')} {option.get('code')}")
        if all(term in name for term in terms) and not any(term in name for term in exclude):
            return option
    return None


def build_azos_recommendation(*, coberturas: list[dict[str, Any]], diagnostico: dict[str, Any]) -> dict[str, Any]:
    income = max(0.0, float(diagnostico.get("renda_mensal") or 0))
    debts = max(0.0, float(diagnostico.get("dividas_saldo") or 0))
    dependents = max(0, int(diagnostico.get("dependentes") or 0))
    children = max(0, int(diagnostico.get("filhos") or 0))
    autonomous = bool(diagnostico.get("autonomo"))
    reserve_months = max(0.0, float(diagnostico.get("reserva_meses") or 0))
    profession = _norm(diagnostico.get("profissao"))
    physical_risk = any(term in profession for term in ("obra", "motorista", "entregador", "tecnico", "mecanico", "eletric", "agric", "personal", "atleta"))

    recommendations: list[dict[str, Any]] = []

    def add(option: dict[str, Any] | None, target: float, reason: str, priority: str) -> None:
        if not option or target <= 0 or any(item["code"] == option.get("code") for item in recommendations):
            return
        recommendations.append({
            "code": option.get("code"),
            "nome": option.get("nome") or option.get("code"),
            "capital": _capital(option, target),
            "motivo": reason,
            "prioridade": priority,
        })

    death_months = 24 + (12 if dependents or children else 0) + (12 if autonomous else 0)
    add(
        _find(coberturas, "morte", exclude=("acident",)),
        max(100_000 if dependents or children else 50_000, income * death_months + debts),
        f"Busca preservar aproximadamente {death_months} meses de renda" + (" e quitar compromissos informados" if debts else "") + ".",
        "essencial" if dependents or children else "importante",
    )
    dg = _find(coberturas, "dg30") or _find(coberturas, "doencas graves", "30") or _find(coberturas, "dg13") or _find(coberturas, "doencas graves")
    add(dg, max(50_000, income * 12), "Cria liquidez para tratamento e reorganização financeira durante uma doença grave.", "essencial")
    ipt = _find(coberturas, "invalidez permanente total", exclude=("acidente", "majorada"))
    add(ipt, max(50_000, income * 24), "Protege a renda se uma invalidez permanente comprometer a atividade e a vida diária.", "essencial" if autonomous else "importante")
    if autonomous:
        rit = _find(coberturas, "renda", "incapacidade") or _find(coberturas, "rit")
        add(rit, income / 22 if income else 0, "Como a renda depende do trabalho, ajuda a manter o caixa durante incapacidade temporária.", "essencial")
    if dependents or children:
        funeral = _find(coberturas, "assistencia funeral") or _find(coberturas, "funeral")
        add(funeral, 15_000, "Evita que despesas funerárias imediatas recaiam sobre a família.", "complementar")
    if physical_risk:
        ref = _find(coberturas, "rupturas") or _find(coberturas, "ref")
        add(ref, max(50_000, income * 6), "A profissão indicada pode sofrer impacto relevante de fraturas ou rupturas de tendões e ligamentos.", "complementar")

    if not recommendations and coberturas:
        first = coberturas[0]
        add(first, max(50_000, income * 12), "Ponto de partida sujeito ao diagnóstico e aos limites liberados pela Azos.", "inicial")

    return {
        "resumo": "A sugestão prioriza continuidade da renda, proteção de dependentes e compromissos financeiros, sem substituir a análise da Azos.",
        "coberturas": recommendations,
        "contexto": {
            "renda_mensal": income,
            "autonomo": autonomous,
            "dependentes": dependents,
            "filhos": children,
            "dividas_saldo": debts,
            "reserva_meses": reserve_months,
            "orcamento_mensal": max(0.0, float(diagnostico.get("orcamento_mensal") or 0)),
        },
        "ajuste": "Os capitais podem subir ou descer conforme orçamento, preferência do cliente e teto efetivamente liberado pela Azos.",
    }
