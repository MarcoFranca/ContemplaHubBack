# app/routers/contract_documents.py
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from supabase import Client

from app.deps import get_supabase_admin
from app.schemas.contract_documents import ContractSignedUrlIn
from app.security.auth import AuthContext
from app.security.permissions import require_auth_context
from app.services.contract_documents_service import (
    delete_contract_document,
    generate_contract_document_signed_url,
    get_contract_document_metadata,
    upload_contract_document,
)

router = APIRouter(prefix="/contracts", tags=["contract-documents"])


@router.get("/{contract_id}/document")
def get_contract_document(
    contract_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_auth_context),
):
    return get_contract_document_metadata(
        supa=supa,
        ctx=ctx,
        contract_id=contract_id,
    )


@router.post("/{contract_id}/document")
async def post_contract_document(
    contract_id: str,
    file: UploadFile = File(...),
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_auth_context),
):
    return await upload_contract_document(
        supa=supa,
        ctx=ctx,
        contract_id=contract_id,
        file=file,
    )


@router.post("/{contract_id}/document/signed-url")
def post_contract_document_signed_url(
    contract_id: str,
    body: ContractSignedUrlIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_auth_context),
):
    return generate_contract_document_signed_url(
        supa=supa,
        ctx=ctx,
        contract_id=contract_id,
        expires_in=body.expires_in,
    )


@router.delete("/{contract_id}/document")
def delete_contract_document_route(
    contract_id: str,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_auth_context),
):
    return delete_contract_document(
        supa=supa,
        ctx=ctx,
        contract_id=contract_id,
    )