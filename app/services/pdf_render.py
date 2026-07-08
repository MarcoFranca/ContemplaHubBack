"""Renderização de página web em PDF via Playwright (API síncrona).

Roda em uma thread separada para não colidir com o event loop do FastAPI
(o webhook do WhatsApp processa de forma síncrona dentro do loop; a API
síncrona do Playwright exige uma thread sem event loop rodando).
"""
from __future__ import annotations

import concurrent.futures
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_MARGIN = {"top": "12mm", "right": "12mm", "bottom": "12mm", "left": "12mm"}


def _render(url: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=90_000)
            return page.pdf(format="A4", print_background=True, margin=_MARGIN)
        finally:
            browser.close()


def render_url_to_pdf(url: str, *, timeout_s: int = 100) -> Optional[bytes]:
    """Renderiza uma URL em PDF. Retorna os bytes ou None em falha."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_render, url).result(timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_render_falhou", extra={"url": url, "error": str(exc)})
        return None
