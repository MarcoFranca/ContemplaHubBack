from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.financeiro import (
    ContratoNumeroUpdateIn,
    FinanceiroContratoOptionsResponse,
    PagamentoListResponse,
    PagamentoUpsertIn,
)
from app.security.auth import AuthContext
from app.security.permissions import require_manager
from app.services.pagamentos_service import (
    create_pagamento,
    list_financeiro_contrato_options,
    list_pagamentos_by_contrato,
    list_pagamentos_by_cota,
    update_contrato_numero,
    update_pagamento,
)

router = APIRouter(prefix="/financeiro", tags=["financeiro"])


def _resolve_org_id(ctx: AuthContext, x_org_id: str | None) -> str:
    if not x_org_id:
        raise HTTPException(400, "X-Org-Id header é obrigatório")
    if x_org_id != ctx.org_id:
        raise HTTPException(403, "Operação cross-org não permitida")
    return ctx.org_id


@router.get("/contratos-options", response_model=FinanceiroContratoOptionsResponse)
def get_financeiro_contratos_options(
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _resolve_org_id(ctx, x_org_id)
    return list_financeiro_contrato_options(supa, org_id=org_id)


@router.post("/pagamentos")
def post_pagamento(
    body: PagamentoUpsertIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _resolve_org_id(ctx, x_org_id)
    return create_pagamento(supa, org_id=org_id, actor_id=ctx.user_id, body=body)


@router.put("/pagamentos/{pagamento_id}")
def put_pagamento(
    pagamento_id: str,
    body: PagamentoUpsertIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _resolve_org_id(ctx, x_org_id)
    return update_pagamento(
        supa,
        org_id=org_id,
        actor_id=ctx.user_id,
        pagamento_id=pagamento_id,
        body=body,
    )


@router.get("/contratos/{contrato_id}/pagamentos", response_model=PagamentoListResponse)
def get_pagamentos_contrato(
    contrato_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _resolve_org_id(ctx, x_org_id)
    return list_pagamentos_by_contrato(supa, org_id=org_id, contrato_id=contrato_id)


@router.get("/cotas/{cota_id}/pagamentos", response_model=PagamentoListResponse)
def get_pagamentos_cota(
    cota_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _resolve_org_id(ctx, x_org_id)
    return list_pagamentos_by_cota(supa, org_id=org_id, cota_id=cota_id)


@router.put("/contratos/{contrato_id}/numero")
def put_contrato_numero(
    contrato_id: str,
    body: ContratoNumeroUpdateIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_manager),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
):
    org_id = _resolve_org_id(ctx, x_org_id)
    return update_contrato_numero(
        supa,
        org_id=org_id,
        contrato_id=contrato_id,
        actor_id=ctx.user_id,
        numero_contrato=body.numero_contrato,
    )
