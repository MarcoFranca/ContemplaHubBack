from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any


_PROFESSION_ALIASES: dict[str, tuple[str, ...]] = {
    "fotograf": ("cinegrafista", "produtor audiovisual", "comunicador visual"),
}


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    text = re.sub(r"\([ao]\)", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _option(item: dict[str, Any]) -> dict[str, Any]:
    name = str(item["nome"])
    button_label = re.sub(r"\(([ao])\)", "", name, flags=re.IGNORECASE)
    button_label = re.sub(r"\s+", " ", button_label).strip()[:20]
    return {"id": item["id"], "nome": name, "rotulo_botao": button_label}


def select_profession_option(options: list[dict[str, Any]], selected_text: str) -> dict[str, Any] | None:
    selected = _normalize(selected_text)
    return next(
        (
            option for option in options
            if selected in {_normalize(option.get("nome")), _normalize(option.get("rotulo_botao"))}
        ),
        None,
    )


def match_azos_professions(
    professions: list[dict[str, Any]], term: str, *, limit: int = 8
) -> tuple[str, list[dict[str, Any]]]:
    """Busca exata primeiro e, se necessário, oferece alternativas sem inventar ID."""
    query = _normalize(term)
    records = [
        {
            "id": item.get("_id") or item.get("id"),
            "nome": item.get("name") or item.get("title"),
            "normalized": _normalize(item.get("name") or item.get("title")),
        }
        for item in professions
        if (item.get("_id") or item.get("id")) and (item.get("name") or item.get("title"))
    ]

    exact = [item for item in records if query == item["normalized"]]
    if exact:
        return "exata", [_option(item) for item in exact[:limit]]

    partial = [item for item in records if query in item["normalized"] or item["normalized"] in query]
    if partial:
        return "alternativa", [_option(item) for item in partial[:3]]

    alias_targets = next(
        (targets for stem, targets in _PROFESSION_ALIASES.items() if stem in query),
        (),
    )
    if alias_targets:
        alternatives = []
        for target in alias_targets:
            match = next((item for item in records if target in item["normalized"]), None)
            if match:
                alternatives.append(_option(match))
        if alternatives:
            return "alternativa", alternatives[:3]

    ranked = sorted(
        (
            (SequenceMatcher(None, query, item["normalized"]).ratio(), item)
            for item in records
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    alternatives = [
        _option(item)
        for score, item in ranked
        if score >= 0.62
    ][:3]
    return ("alternativa" if alternatives else "nao_encontrada"), alternatives
