from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.carteira_import import (
    CarteiraImportConfirmRequest,
    CarteiraImportConfirmResponse,
    CarteiraImportPreviewRequest,
    CarteiraImportPreviewResponse,
)
from app.security.auth import CurrentProfile, get_current_profile
from app.services.carteira_import_service import build_import_preview, confirm_import


router = APIRouter(prefix="/carteira/import", tags=["carteira-import"])


def _require_manager(profile: CurrentProfile) -> None:
    if not profile.is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A importação em massa é permitida apenas para admin ou gestor.",
        )


@router.post("/preview", response_model=CarteiraImportPreviewResponse)
def carteira_import_preview(
    body: CarteiraImportPreviewRequest,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    _require_manager(profile)
    _, response = build_import_preview(
        sb=sb,
        profile=profile,
        raw_text=body.raw_text,
        produto_padrao=body.produto_padrao,
    )
    return response


@router.post("/confirm", response_model=CarteiraImportConfirmResponse)
def carteira_import_confirm(
    body: CarteiraImportConfirmRequest,
    sb: Client = Depends(get_supabase_admin),
    profile: CurrentProfile = Depends(get_current_profile),
):
    _require_manager(profile)
    return confirm_import(
        sb=sb,
        profile=profile,
        raw_text=body.raw_text,
        produto_padrao=body.produto_padrao,
    )

