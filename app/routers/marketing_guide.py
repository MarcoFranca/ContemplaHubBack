# app/routers/marketing_guide.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from supabase import Client

from app.deps import get_supabase_admin
from app.core.config import settings
from app.schemas.marketing_guide import GuideSubmitIn, GuideSubmitOut
from app.services.marketing_guide_service import (
    submit_guide_lead,
    generate_guide_signed_url,
)

router = APIRouter(prefix="/api/marketing/guide", tags=["marketing-guide"])


@router.post("/submit", response_model=GuideSubmitOut)
async def submit(
    body: GuideSubmitIn,
    req: Request,
    supa: Client = Depends(get_supabase_admin),
):
    """
    Captura lead da landing (visitante, não usuário autenticado),
    vincula ao owner da landing e grava consentimento.
    """
    if not body.consentimento:
        raise HTTPException(status_code=400, detail="consent_required")

    # captura IP/UA
    xff = req.headers.get("x-forwarded-for", "")
    ip = (xff.split(",")[0].strip() if xff else None) or req.headers.get("x-real-ip")
    ua = body.user_agent or req.headers.get("user-agent")

    env_org_id = getattr(settings, "MARKETING_ORG_ID", None) or None
    env_owner_id = getattr(settings, "MARKETING_OWNER_ID", None) or None
    env_landing_id = getattr(settings, "MARKETING_LANDING_ID", None) or None

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
            env_org_id=env_org_id,
            env_owner_id=env_owner_id,
            env_landing_id=env_landing_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"submit_failed: {str(e)}")

    return GuideSubmitOut(lead_id=lead_id)


@router.get("/download")
async def download(
    lead_id: str,
    mode: str = "redirect",
    supa: Client = Depends(get_supabase_admin),
):
    """
    Gera URL assinada do PDF e:
      - mode=redirect (padrão): redireciona para a URL
      - mode=json: retorna {"signed_url": "..."}
    """
    bucket = getattr(settings, "MARKETING_GUIDE_BUCKET", "marketing")
    path_template = getattr(
        settings,
        "MARKETING_GUIDE_PATH_TEMPLATE",
        "orgs/{org_id}/guides/guia-estrategico-consorcio-v1.pdf",
    )

    try:
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
