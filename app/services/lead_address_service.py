from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

ADDRESS_FIELDS = (
    "cep",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cidade",
    "estado",
    "latitude",
    "longitude",
)

LEAD_ADDRESS_SELECT = ", ".join((*ADDRESS_FIELDS, "address_updated_at"))


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return str(value)


def normalize_address_value(field: str, value: Any) -> Any:
    if field == "cep":
        cleaned = "".join(ch for ch in str(value or "") if ch.isdigit())
        return cleaned or None

    if field == "estado":
        cleaned = _clean_text(value)
        return cleaned.upper() if cleaned else None

    if field in {"latitude", "longitude"}:
        if value is None or value == "":
            return None
        return float(value)

    return _clean_text(value)


def apply_lead_address_rules(
    payload: Mapping[str, Any],
    *,
    current_lead: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = dict(payload)
    address_payload = {
        field: normalize_address_value(field, payload[field])
        for field in ADDRESS_FIELDS
        if field in payload
    }

    normalized.update(address_payload)

    if not address_payload:
        return normalized

    if current_lead is None:
        changed = any(value is not None for value in address_payload.values())
    else:
        changed = any(
            normalize_address_value(field, current_lead.get(field)) != value
            for field, value in address_payload.items()
        )

    if changed:
        normalized["address_updated_at"] = datetime.now(timezone.utc).isoformat()

    return normalized
