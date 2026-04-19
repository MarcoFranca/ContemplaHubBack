# app/routers/leads.py
from fastapi import APIRouter, HTTPException, Depends, Header, status
from pydantic import BaseModel, field_validator
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.kanban import Stage
from app.schemas.leads import LeadCreateIn, LeadOut, LeadUpdateIn
from app.services.lead_address_service import LEAD_ADDRESS_SELECT, apply_lead_address_rules
from app.services.kanban_service import move_lead_stage


class MoveStageIn(BaseModel):
    stage: Stage
    reason: str | None = None

    @field_validator("stage")
    @classmethod
    def not_empty(cls, v: Stage) -> Stage:
        if not v:
            raise ValueError("stage é obrigatório")
        return v


router = APIRouter(prefix="/leads", tags=["leads"])


LEAD_SELECT = (
    "id, org_id, nome, telefone, email, origem, owner_id, etapa, created_at, "
    "updated_at, first_contact_at, "
    f"{LEAD_ADDRESS_SELECT}"
)


def _require_org_id(x_org_id: str | None) -> str:
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )
    return x_org_id


def _get_lead_or_404(*, supa: Client, org_id: str, lead_id: str) -> dict:
    resp = (
        supa.table("leads")
        .select(LEAD_SELECT)
        .eq("org_id", org_id)
        .eq("id", lead_id)
        .maybe_single()
        .execute()
    )
    lead = getattr(resp, "data", None)
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead não encontrado para esta organização.",
        )
    return lead


@router.post("", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
def create_lead(
    body: LeadCreateIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _require_org_id(x_org_id)
    payload = apply_lead_address_rules(
        {
            "org_id": org_id,
            **body.model_dump(mode="json", exclude_none=True),
        }
    )

    resp = (
        supa.table("leads")
        .insert(payload, returning="representation")
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao criar lead.",
        )

    return rows[0]


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: str,
    body: LeadUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _require_org_id(x_org_id)
    current_lead = _get_lead_or_404(supa=supa, org_id=org_id, lead_id=lead_id)
    payload = apply_lead_address_rules(
        body.model_dump(mode="json", exclude_unset=True),
        current_lead=current_lead,
    )

    resp = (
        supa.table("leads")
        .update(payload)
        .eq("org_id", org_id)
        .eq("id", lead_id)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao atualizar lead.",
        )

    return rows[0]


@router.patch("/{lead_id}/stage")
def move_stage(
    lead_id: str,
    body: MoveStageIn,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    """
    Move a etapa de um lead, delegando a lógica ao serviço de Kanban.
    """
    x_org_id = _require_org_id(x_org_id)

    result = move_lead_stage(
        org_id=x_org_id,
        lead_id=lead_id,
        new_stage=body.stage,
        supa=supa,
        reason=body.reason,
    )

    if not result.get("ok"):
        error = result.get("error")
        if error == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.get("message"))
        if error == "forbidden":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result.get("message"))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result.get("message"))

    return result


@router.delete("/{lead_id}", status_code=204)
def api_delete_lead(
    lead_id: str,
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    x_org_id = _require_org_id(x_org_id)

    try:
        resp = (
            supa.table("leads")
            .delete()
            .eq("org_id", x_org_id)
            .eq("id", lead_id)
            .execute()
        )

        data = getattr(resp, "data", None) or []
        # Se quiser ser mais estrito, dá pra retornar 404 se não deletou nada:
        if not data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead não encontrado para esta organização.",
            )

        # 204 No Content
        return

    except HTTPException:
        # Repassa HTTPExceptions que você mesmo levantou
        raise
    except Exception as e:
        print("\n\nERRO ao deletar lead:", repr(e), "\n\n")
        raise HTTPException(
            status_code=500,
            detail="Erro ao deletar lead e registros associados.",
        )
