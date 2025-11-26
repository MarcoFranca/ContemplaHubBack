# app/routers/leads.py
from fastapi import APIRouter, HTTPException, Depends, Header, status
from pydantic import BaseModel, field_validator
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.kanban import Stage
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
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )

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
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )

    try:
        # 1) Apagar propostas
        supa.table("lead_propostas") \
            .delete() \
            .eq("org_id", x_org_id) \
            .eq("lead_id", lead_id) \
            .execute()

        # 2) Apagar diagnósticos
        supa.table("lead_diagnosticos") \
            .delete() \
            .eq("org_id", x_org_id) \
            .eq("lead_id", lead_id) \
            .execute()

        # 3) Buscar todas as cotas do lead
        resp_cotas = supa.table("cotas") \
            .select("id") \
            .eq("org_id", x_org_id) \
            .eq("lead_id", lead_id) \
            .execute()

        cotas = resp_cotas.data or []

        # 4) Apagar contratos dessas cotas
        for c in cotas:
            supa.table("contratos") \
                .delete() \
                .eq("org_id", x_org_id) \
                .eq("cota_id", c["id"]) \
                .execute()

        # 5) Apagar cotas do lead
        supa.table("cotas") \
            .delete() \
            .eq("org_id", x_org_id) \
            .eq("lead_id", lead_id) \
            .execute()

        # 6) Apagar histórico de Kanban
        supa.table("kanban_history") \
            .delete() \
            .eq("org_id", x_org_id) \
            .eq("lead_id", lead_id) \
            .execute()

        # 7) Finalmente apagar o lead
        supa.table("leads") \
            .delete() \
            .eq("org_id", x_org_id) \
            .eq("id", lead_id) \
            .execute()

        return

    except Exception as e:
        print("ERRO ao deletar lead:", repr(e))
        raise HTTPException(
            status_code=500,
            detail="Erro ao deletar lead e registros associados.",
        )

