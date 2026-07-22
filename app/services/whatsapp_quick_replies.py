from __future__ import annotations

import re


_QUICK_BUTTONS_RE = re.compile(r"\s*\[\[BOTOES:([^\]\r\n]+)\]\]\s*$", re.IGNORECASE)


def extract_quick_replies(text: str) -> tuple[str, list[str]]:
    """Remove o marcador interno da IA e devolve opções válidas para botões."""
    content = (text or "").strip()
    match = _QUICK_BUTTONS_RE.search(content)
    if not match:
        return content, []
    body = content[:match.start()].strip() or "Escolha uma opção:"
    raw_buttons = [option.strip() for option in match.group(1).split("|") if option.strip()]
    if not 2 <= len(raw_buttons) <= 3:
        fallback = f"{body}\n\nResponda com: {' / '.join(raw_buttons)}" if raw_buttons else body
        return fallback, []
    buttons = [option[:20] for option in raw_buttons]
    return body, buttons
