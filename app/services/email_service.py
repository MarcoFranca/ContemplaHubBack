# app/services/email_service.py
from __future__ import annotations

import os
import requests


RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "ContemplaHub <no-reply@corretorlab.com>")


class EmailSendError(Exception):
    pass


def send_system_email(
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    """
    Envia e-mail transacional via Resend.

    - `text_body` sempre vai junto (fallback para e-mail sem HTML).
    - `html_body` é opcional, mas recomendado pra ficar bonito.
    """

    if not RESEND_API_KEY:
        print("WARN: RESEND_API_KEY não configurada, não vou enviar e-mail.")
        print("Destino:", to)
        print("Assunto:", subject)
        print("Texto:\n", text_body)
        return

    payload: dict = {
        "from": RESEND_FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "text": text_body or "",
    }

    if html_body:
        payload["html"] = html_body

    resp = requests.post(
        "https://api.resend.com/emails",
        json=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )

    if resp.status_code >= 400:
        print("ERRO Resend:", resp.status_code, resp.text)
        raise EmailSendError(
            f"Resend retornou status {resp.status_code}: {resp.text}"
        )

    # Se quiser logar o id do e-mail:
    try:
        data = resp.json()
        print("Resend Email ID:", data.get("id"))
    except Exception:
        pass
