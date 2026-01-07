# app/routers/marketing_guide.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from supabase import Client

from app.deps import get_supabase_admin
from app.core.config import settings
from app.schemas.marketing_guide import GuideSubmitIn, GuideSubmitOut
from app.services.marketing_guide_service import (
    submit_guide_lead,
    generate_guide_signed_url, ensure_guide_pdf_exists,
)

router = APIRouter(prefix="/api/marketing/guide", tags=["marketing-guide"])


def _get_ip(req: Request) -> str | None:
    xff = req.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip() or None
    return req.headers.get("x-real-ip")


@router.post("/submit", response_model=GuideSubmitOut)
async def submit(
    body: GuideSubmitIn,
    req: Request,
    supa: Client = Depends(get_supabase_admin),
):
    """
    Captura LEAD (visitante), resolve org/owner dinamicamente via landing_pages
    e registra consentimento.
    """
    if not body.consentimento:
        raise HTTPException(status_code=400, detail="consent_required")

    ip = _get_ip(req)
    ua = body.user_agent or req.headers.get("user-agent")

    try:
        lead_id = submit_guide_lead(
            supa=supa,
            landing_slug=body.landing_slug,
            landing_hash=body.landing_hash,
            nome=body.nome,
            telefone=body.telefone,
            email=str(body.email) if body.email else None,
            consent_scope=body.consent_scope,
            utm={
                "utm_source": body.utm_source,
                "utm_medium": body.utm_medium,
                "utm_campaign": body.utm_campaign,
                "utm_term": body.utm_term,
                "utm_content": body.utm_content,
            },
            referrer_url=body.referrer_url,
            user_agent=ua,
            ip=ip,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"submit_failed: {str(e)}")

    return GuideSubmitOut(lead_id=lead_id)


@router.get("/download")
async def download(
    lead_id: str,
    mode: str = "redirect",
    supa: Client = Depends(get_supabase_admin),
):
    bucket = getattr(settings, "MARKETING_GUIDE_BUCKET", "marketing")
    path_template = getattr(
        settings,
        "MARKETING_GUIDE_PATH_TEMPLATE",
        "orgs/{org_id}/guides/guia-estrategico-consorcio-v1.pdf",
    )

    try:
        # 1) garante que o PDF exista (gera e faz upload se necessário)
        await ensure_guide_pdf_exists(
            supa=supa,
            lead_id=lead_id,
            bucket=bucket,
            path_template=path_template,
        )

        # 2) assina e redireciona
        signed_url = generate_guide_signed_url(
            supa=supa,
            lead_id=lead_id,
            bucket=bucket,
            path_template=path_template,
            expires_in=300,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"download_failed: {str(e)}")

    if mode == "json":
        return {"signed_url": signed_url, "expires_in_seconds": 300}

    return RedirectResponse(signed_url)
