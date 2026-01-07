# app/services/marketing_guide_service.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from supabase import Client
from playwright.async_api import async_playwright


def _safe_upsert_lead(
    supa: Client,
    org_id: str,
    telefone: str,
    payload_full: Dict[str, Any],
    payload_min: Dict[str, Any],
) -> str:
    existing = (
        supa.table("leads")
        .select("id, owner_id")
        .eq("org_id", org_id)
        .eq("telefone", telefone)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        lead_id = existing[0]["id"]

        if existing[0].get("owner_id"):
            payload_full.pop("owner_id", None)
            payload_min.pop("owner_id", None)

        try:
            supa.table("leads").update(payload_full).eq("id", lead_id).execute()
        except Exception:
            supa.table("leads").update(payload_min).eq("id", lead_id).execute()

        return lead_id

    try:
        inserted = supa.table("leads").insert(payload_full).execute().data or []
    except Exception:
        inserted = supa.table("leads").insert(payload_min).execute().data or []

    if not inserted:
        raise RuntimeError("Falha ao inserir lead (retorno vazio).")

    return inserted[0]["id"]


def _safe_insert_consent(
    supa: Client,
    consent_full: Dict[str, Any],
    consent_min: Dict[str, Any],
) -> None:
    try:
        supa.table("consent_logs").insert(consent_full).execute()
    except Exception:
        supa.table("consent_logs").insert(consent_min).execute()


def resolve_landing_context(
    supa: Client,
    *,
    landing_slug: Optional[str],
    landing_hash: Optional[str],
) -> Tuple[str, str, str]:
    if not landing_hash and not landing_slug:
        raise ValueError("landing_hash (public_hash) ou landing_slug (slug) é obrigatório.")

    q = (
        supa.table("landing_pages")
        .select("id, org_id, owner_user_id, active")
        .eq("active", True)
        .limit(1)
    )

    if landing_hash:
        q = q.eq("public_hash", landing_hash)
    else:
        q = q.eq("slug", landing_slug)

    data = q.execute().data or []
    if not data:
        raise LookupError("Landing não encontrada ou inativa.")

    row = data[0]
    return row["org_id"], row["owner_user_id"], row["id"]


def submit_guide_lead(
    supa: Client,
    *,
    landing_slug: Optional[str],
    landing_hash: Optional[str],
    nome: str,
    telefone: str,
    email: Optional[str],
    consent_scope: str,
    utm: Dict[str, Optional[str]],
    referrer_url: Optional[str],
    user_agent: Optional[str],
    ip: Optional[str],
) -> str:
    org_id, owner_id, landing_id = resolve_landing_context(
        supa=supa,
        landing_slug=landing_slug,
        landing_hash=landing_hash,
    )

    payload_full: Dict[str, Any] = {
        "org_id": org_id,
        "owner_id": owner_id,
        "landing_id": landing_id,
        "nome": nome,
        "telefone": telefone,
        "email": email,
        "origem": "lp",
        "etapa": "novo",
        "referrer_url": referrer_url,
        "user_agent": user_agent,
        "utm_source": utm.get("utm_source"),
        "utm_medium": utm.get("utm_medium"),
        "utm_campaign": utm.get("utm_campaign"),
        "utm_term": utm.get("utm_term"),
        "utm_content": utm.get("utm_content"),
    }

    payload_min: Dict[str, Any] = {
        "org_id": org_id,
        "owner_id": owner_id,
        "landing_id": landing_id,
        "nome": nome,
        "telefone": telefone,
        "email": email,
        "origem": "lp",
        "etapa": "novo",
    }

    lead_id = _safe_upsert_lead(
        supa=supa,
        org_id=org_id,
        telefone=telefone,
        payload_full=payload_full,
        payload_min=payload_min,
    )

    consent_full = {
        "org_id": org_id,
        "lead_id": lead_id,
        "consentimento": True,
        "scope": consent_scope,
        "ip": ip,
        "user_agent": user_agent,
    }
    consent_min = {
        "lead_id": lead_id,
        "consentimento": True,
        "scope": consent_scope,
    }

    _safe_insert_consent(supa, consent_full=consent_full, consent_min=consent_min)

    return lead_id


def _require_consent(supa: Client, lead_id: str) -> None:
    consent = (
        supa.table("consent_logs")
        .select("id")
        .eq("lead_id", lead_id)
        .eq("consentimento", True)
        .limit(1)
        .execute()
    ).data or []
    if not consent:
        raise PermissionError("Lead sem consentimento para baixar o material.")


def _get_lead_org_and_landing_hash(supa: Client, lead_id: str) -> tuple[str, str]:
    """
    Retorna (org_id, public_hash) a partir do lead.
    Precisamos do public_hash para montar a URL do HTML print.
    """
    lead = (
        supa.table("leads")
        .select("org_id, landing_id")
        .eq("id", lead_id)
        .limit(1)
        .execute()
    ).data or []

    if not lead:
        raise LookupError("Lead não encontrado.")

    org_id = lead[0]["org_id"]
    landing_id = lead[0].get("landing_id")

    if not landing_id:
        # fallback: se por algum motivo o lead não salvou landing_id
        raise LookupError("Lead sem landing_id para gerar o PDF.")

    lp = (
        supa.table("landing_pages")
        .select("public_hash")
        .eq("id", landing_id)
        .limit(1)
        .execute()
    ).data or []

    if not lp or not lp[0].get("public_hash"):
        raise LookupError("Landing não encontrada para este lead.")

    return org_id, lp[0]["public_hash"]


async def _render_pdf_from_frontend(url: str) -> bytes:
    """
    Renderiza a URL HTML do guia e retorna bytes do PDF.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=90_000)
        pdf_bytes = await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "14mm", "right": "14mm", "bottom": "14mm", "left": "14mm"},
        )
        await browser.close()
        return pdf_bytes


async def ensure_guide_pdf_exists(
    supa: Client,
    *,
    lead_id: str,
    bucket: str,
    path_template: str,
) -> tuple[str, str]:
    """
    Garante que o PDF exista no Storage. Se não existir, gera a partir do HTML print e faz upload.
    Retorna (org_id, path).
    """
    _require_consent(supa, lead_id)

    org_id, landing_hash = _get_lead_org_and_landing_hash(supa, lead_id)
    path = path_template.format(org_id=org_id)

    # tenta verificar existência via signed URL rápido (se não existir, a abertura falha)
    # Supabase storage não tem "exists" universal no client; então tentamos gerar signed e,
    # se der erro, geramos e upamos.
    try:
        # tentativa de signed url (pode falhar se arquivo não existir)
        signed = supa.storage.from_(bucket).create_signed_url(path, 60)
        url = signed.get("signedURL") or signed.get("signedUrl")
        if url:
            return org_id, path
    except Exception:
        pass

    # não existe / falhou -> gerar
    front_base = os.getenv("FRONTEND_APP_URL")
    if not front_base:
        raise RuntimeError("FRONTEND_APP_URL não definido no backend.")
    front_base = front_base.rstrip("/")

    html_url = f"{front_base}/guia-consorcio/print?lp={landing_hash}"

    pdf_bytes = await _render_pdf_from_frontend(html_url)

    # upload (upsert)
    supa.storage.from_(bucket).upload(
        path,
        pdf_bytes,
        file_options={"content-type": "application/pdf", "upsert": "true"},
    )

    return org_id, path


def generate_guide_signed_url(
    supa: Client,
    *,
    lead_id: str,
    bucket: str,
    path_template: str,
    expires_in: int = 300,
) -> str:
    """
    ASSINA URL do PDF (pressupõe que o arquivo já existe).
    """
    _require_consent(supa, lead_id)

    lead = (
        supa.table("leads")
        .select("org_id")
        .eq("id", lead_id)
        .limit(1)
        .execute()
    ).data or []

    if not lead:
        raise LookupError("Lead não encontrado.")

    org_id = lead[0]["org_id"]
    path = path_template.format(org_id=org_id)

    signed = supa.storage.from_(bucket).create_signed_url(path, expires_in)
    url = signed.get("signedURL") or signed.get("signedUrl")
    if not url:
        raise RuntimeError("Falha ao gerar signed URL.")

    return url
