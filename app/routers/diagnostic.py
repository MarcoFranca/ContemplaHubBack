# app/routers/diagnostic.py
from fastapi import APIRouter, Depends, Header, HTTPException, status
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.diagnostic import (
    DiagnosticInput,
    DiagnosticResponse,
    DiagnosticRecord,
)
from app.services.diagnostic_service import (
    save_diagnostic,
    get_diagnostic,
)

router = APIRouter(prefix="/diagnostico", tags=["diagnostico"])


@router.get("/{lead_id}", response_model=DiagnosticRecord)
def get_diagnostic_route(
    lead_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )

    record = get_diagnostic(org_id=x_org_id, lead_id=lead_id, supa=supa)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnóstico não encontrado para este lead",
        )
    return record


@router.post("/{lead_id}", response_model=DiagnosticResponse)
def save_diagnostic_route(
    lead_id: str,
    body: DiagnosticInput,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )

    # TODO: opcionalmente validar se o lead existe e pertence ao org_id

    result = save_diagnostic(org_id=x_org_id, lead_id=lead_id, input=body, supa=supa)
    return result
