# app/routers/marketing_guide_pdf.py
from __future__ import annotations

import os
from fastapi import APIRouter, Depends, HTTPException, Header
from supabase import Client
from playwright.async_api import async_playwright

from app.deps import get_supabase_admin

router = APIRouter(prefix="/api/marketing/guide", tags=["marketing-guide-admin"])


def _require_internal_token(x_internal_token: str | None) -> None:
    expected = os.getenv("INTERNAL_PDF_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="missing_INTERNAL_PDF_TOKEN")
    if not x_internal_token or x_internal_token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/build-pdf")
async def build_pdf(
    landing_hash: str,
    supa: Client = Depends(get_supabase_admin),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """
    Gera o PDF a partir da rota /guia-consorcio/print no FRONT e faz upload no Storage.

    Protegido por header: X-Internal-Token = INTERNAL_PDF_TOKEN
    """
    _require_internal_token(x_internal_token)

    # 1) resolve landing -> org_id
    landing = (
        supa.table("landing_pages")
        .select("org_id")
        .eq("public_hash", landing_hash)
        .eq("active", True)
        .limit(1)
        .execute()
    ).data or []

    if not landing:
        raise HTTPException(status_code=404, detail="landing_not_found")

    org_id = landing[0]["org_id"]

    # 2) URL do print no Next (precisa ser URL pública do FRONT)
    front_base_url = os.getenv("FRONTEND_BASE_URL")
    if not front_base_url:
        raise HTTPException(status_code=500, detail="missing_FRONTEND_BASE_URL")

    front_base_url = front_base_url.rstrip("/")
    url = f"{front_base_url}/guia-consorcio/print?lp={landing_hash}"

    # 3) gerar PDF
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            page = await browser.new_page()

            # importante: timeout razoável em produção
            await page.goto(url, wait_until="networkidle", timeout=90_000)

            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "14mm", "right": "14mm", "bottom": "14mm", "left": "14mm"},
            )

            await browser.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"pdf_generation_failed: {str(e)}")

    # 4) upload storage (bucket e path alinhados ao /download)
    bucket = os.getenv("MARKETING_GUIDE_BUCKET", "marketing")
    path = f"orgs/{org_id}/guides/guia-estrategico-consorcio-v1.pdf"

    try:
        # supabase-py v2: upload(path, file, file_options)
        supa.storage.from_(bucket).upload(
            path,
            pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"storage_upload_failed: {str(e)}")

    return {"ok": True, "bucket": bucket, "path": path, "source_url": url}
