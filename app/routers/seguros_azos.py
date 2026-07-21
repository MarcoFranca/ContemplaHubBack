from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.seguros_azos import AzosCoberturasIn, AzosCotacaoIn, AzosSyncIn
from app.security.auth import AuthContext
from app.security.permissions import require_internal_user, require_manager
from app.services.azos_service import create_quote, ensure_lead, get_azos_client, sync_resource


router = APIRouter(prefix="/seguros/azos", tags=["seguros-azos"])


def _org(ctx: AuthContext, x_org_id: str | None) -> str:
    if not x_org_id:
        raise HTTPException(400, "X-Org-Id header é obrigatório")
    if ctx.org_id != x_org_id:
        raise HTTPException(403, "Operação cross-org não permitida")
    return ctx.org_id


@router.get("/profissoes")
def get_profissoes_azos(
    ctx: AuthContext = Depends(require_internal_user),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    _org(ctx, x_org_id)
    return get_azos_client().list_professions()


@router.post("/leads/{lead_id}/coberturas")
def post_coberturas_azos(
    lead_id: str,
    body: AzosCoberturasIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_internal_user),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _org(ctx, x_org_id)
    ensure_lead(supa, org_id=org_id, lead_id=lead_id)
    return get_azos_client().list_coverages(body.perfil.to_azos())


@router.post("/leads/{lead_id}/cotacoes")
def post_cotacao_azos(
    lead_id: str,
    body: AzosCotacaoIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_internal_user),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _org(ctx, x_org_id)
    return create_quote(
        supa,
        org_id=org_id,
        lead_id=lead_id,
        created_by=ctx.user_id,
        profile=body.perfil.to_azos(),
        selected_coverages=[item.model_dump() for item in body.coberturas],
        azos=get_azos_client(),
    )


@router.post("/sincronizar")
def post_sincronizar_azos(
    body: AzosSyncIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _org(ctx, x_org_id)
    return sync_resource(
        supa,
        org_id=org_id,
        resource=body.recurso,
        limit=body.limit,
        offset=body.offset,
        azos=get_azos_client(),
    )

