# app/routers/marketing_guide_pdf.py
from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
from app.deps import get_supabase_admin
from playwright.async_api import async_playwright

router = APIRouter(prefix="/api/marketing/guide", tags=["marketing-guide-admin"])

@router.post("/build-pdf")
async def build_pdf(landing_hash: str, supa: Client = Depends(get_supabase_admin)):
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

    # 2) URL do print no Next
    base_lp = "http://localhost:3000"  # troque por env em prod
    url = f"{base_lp}/guia-consorcio/print?lp={landing_hash}"

    # 3) gerar PDF
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")

        pdf_bytes = await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "14mm", "right": "14mm", "bottom": "14mm", "left": "14mm"},
        )
        await browser.close()

    # 4) upload storage
    bucket = "marketing"
    path = f"orgs/{org_id}/guides/guia-estrategico-consorcio-v1.pdf"
    supa.storage.from_(bucket).upload(
        path,
        pdf_bytes,
        {"content-type": "application/pdf", "upsert": "true"},
    )

    return {"ok": True, "bucket": bucket, "path": path}
