# app/services/email_service.py
from __future__ import annotations

import os
import requests


RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv(
    "RESEND_FROM_EMAIL",
    "ContemplaHub <no-reply@corretorlab.com>",
)


class EmailSendError(Exception):
    """Erro ao enviar e-mail via Resend."""


def send_system_email(to: str, subject: str, text_body: str) -> None:
    """
    Envia um e-mail transacional simples usando a API do Resend.
    """

    if not RESEND_API_KEY:
        # Modo "degradado": não quebra a aplicação, só loga.
        print("WARN: RESEND_API_KEY não configurada. E-mail NÃO enviado.")
        print("Destino:", to)
        print("Assunto:", subject)
        print("Corpo:\n", text_body)
        return

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "text": text_body,
    }

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
    except Exception as e:
        print("ERRO ao chamar Resend:", repr(e))
        raise EmailSendError("Falha ao comunicar com o serviço de e-mail") from e

    if resp.status_code >= 400:
        print("ERRO Resend:", resp.status_code, resp.text)
        raise EmailSendError(
            f"Resend retornou status {resp.status_code}: {resp.text}"
        )

    # Se chegou aqui, está tudo certo.
    print("E-mail enviado com sucesso via Resend para", to)
