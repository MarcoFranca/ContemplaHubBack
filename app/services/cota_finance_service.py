from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from fastapi import HTTPException


TAXA_ADMIN_ANTECIPADA_FORMAS = {"avista", "parcelado"}

FUNDO_RESERVA_FIELDS = (
    "fundo_reserva_percentual",
    "fundo_reserva_valor_mensal",
)

SEGURO_PRESTAMISTA_FIELDS = (
    "seguro_prestamista_ativo",
    "seguro_prestamista_percentual",
    "seguro_prestamista_valor_mensal",
)

TAXA_ADMIN_ANTECIPADA_FIELDS = (
    "taxa_admin_antecipada_ativo",
    "taxa_admin_antecipada_percentual",
    "taxa_admin_antecipada_forma_pagamento",
    "taxa_admin_antecipada_parcelas",
    "taxa_admin_antecipada_valor_total",
    "taxa_admin_antecipada_valor_parcela",
)

COTA_FINANCIAL_FIELDS = (
    *FUNDO_RESERVA_FIELDS,
    *SEGURO_PRESTAMISTA_FIELDS,
    *TAXA_ADMIN_ANTECIPADA_FIELDS,
)

COTA_FINANCIAL_SELECT = ", ".join(COTA_FINANCIAL_FIELDS)


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "sim", "yes"}:
            return True
        if lowered in {"false", "0", "nao", "não", "no"}:
            return False
    return bool(value)


def _to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if "," in normalized and "." in normalized:
            normalized = normalized.replace(".", "").replace(",", ".")
        elif "," in normalized:
            normalized = normalized.replace(",", ".")
        try:
            return float(normalized)
        except ValueError as exc:
            raise HTTPException(400, f"Valor numérico inválido: {value}") from exc
    raise HTTPException(400, f"Valor numérico inválido: {value}")


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"Valor inteiro inválido: {value}") from exc


def normalize_cota_financial_payload(
    payload: Mapping[str, Any],
    *,
    current_cota: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = dict(payload)

    for field in (
        "fundo_reserva_percentual",
        "fundo_reserva_valor_mensal",
        "seguro_prestamista_percentual",
        "seguro_prestamista_valor_mensal",
        "taxa_admin_antecipada_percentual",
        "taxa_admin_antecipada_valor_total",
        "taxa_admin_antecipada_valor_parcela",
    ):
        if field in payload:
            normalized[field] = _to_number(payload[field])

    if "taxa_admin_antecipada_parcelas" in payload:
        normalized["taxa_admin_antecipada_parcelas"] = _to_int(payload["taxa_admin_antecipada_parcelas"])

    if "seguro_prestamista_ativo" in payload:
        normalized["seguro_prestamista_ativo"] = _to_bool(payload["seguro_prestamista_ativo"])

    if "taxa_admin_antecipada_ativo" in payload:
        normalized["taxa_admin_antecipada_ativo"] = _to_bool(payload["taxa_admin_antecipada_ativo"])

    seguro_ativo = normalized.get(
        "seguro_prestamista_ativo",
        current_cota.get("seguro_prestamista_ativo") if current_cota else None,
    )
    if seguro_ativo is False:
        normalized["seguro_prestamista_percentual"] = None
        normalized["seguro_prestamista_valor_mensal"] = None

    taxa_ativo = normalized.get(
        "taxa_admin_antecipada_ativo",
        current_cota.get("taxa_admin_antecipada_ativo") if current_cota else None,
    )
    if taxa_ativo is False:
        normalized["taxa_admin_antecipada_forma_pagamento"] = None
        normalized["taxa_admin_antecipada_parcelas"] = None
        normalized["taxa_admin_antecipada_percentual"] = None
        normalized["taxa_admin_antecipada_valor_total"] = None
        normalized["taxa_admin_antecipada_valor_parcela"] = None
        return normalized

    forma = normalized.get(
        "taxa_admin_antecipada_forma_pagamento",
        current_cota.get("taxa_admin_antecipada_forma_pagamento") if current_cota else None,
    )
    if forma is not None:
        forma = str(forma).strip().lower()
        if forma not in TAXA_ADMIN_ANTECIPADA_FORMAS:
            raise HTTPException(
                400,
                "taxa_admin_antecipada_forma_pagamento deve ser 'avista' ou 'parcelado'",
            )
        normalized["taxa_admin_antecipada_forma_pagamento"] = forma

    parcelas = normalized.get(
        "taxa_admin_antecipada_parcelas",
        current_cota.get("taxa_admin_antecipada_parcelas") if current_cota else None,
    )
    parcelas_int = _to_int(parcelas)

    if taxa_ativo:
        if forma == "avista":
            normalized["taxa_admin_antecipada_parcelas"] = 1
        elif forma == "parcelado":
            if parcelas_int is None or parcelas_int <= 1:
                raise HTTPException(
                    400,
                    "taxa_admin_antecipada_parcelas deve ser maior que 1 quando o pagamento for parcelado",
                )
            normalized["taxa_admin_antecipada_parcelas"] = parcelas_int

    return normalized
