# app/routers/kanban.py
from fastapi import APIRouter, Depends, Header, HTTPException, status
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.kanban import KanbanSnapshot, KanbanMetrics
from app.services.kanban_service import (
    build_kanban_snapshot,
    get_kanban_metrics,
)

router = APIRouter(prefix="/kanban", tags=["kanban"])


@router.get("", response_model=KanbanSnapshot)
def get_kanban(
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    show_active: bool = False,
    show_lost: bool = False,
):
    """
    Retorna o snapshot do Kanban (colunas => leads).
    """
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )

    snapshot = build_kanban_snapshot(
        org_id=x_org_id,
        supa=supa,
        show_active=show_active,
        show_lost=show_lost,
    )
    return snapshot


@router.get("/metrics", response_model=KanbanMetrics)
def get_kanban_metrics_route(
    supa: Client = Depends(get_supabase_admin),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    """
    Retorna as métricas agregadas do Kanban usando a função SQL get_kanban_metrics.
    """
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id header é obrigatório por enquanto",
        )

    metrics = get_kanban_metrics(org_id=x_org_id, supa=supa)
    return metrics
