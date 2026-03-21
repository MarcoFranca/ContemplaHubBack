# app/services/contract_documents_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, UploadFile, status
from supabase import Client

from app.core.config import settings
from app.security.auth import AuthContext


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_data(resp: Any) -> Any:
    return getattr(resp, "data", None)


def insert_audit_log(
    supa: Client,
    *,
    org_id: str,
    actor_id: str,
    entity: str,
    entity_id: Optional[str],
    action: str,
    diff: Optional[dict] = None,
) -> None:
    try:
        supa.table("audit_logs").insert(
            {
                "org_id": org_id,
                "actor_id": actor_id,
                "entity": entity,
                "entity_id": entity_id,
                "action": action,
                "diff": diff or {},
            }
        ).execute()
    except Exception:
        # Auditoria não deve derrubar a operação principal
        pass


def get_contract_or_404(
    supa: Client,
    *,
    org_id: str,
    contract_id: str,
) -> Dict[str, Any]:
    resp = (
        supa.table("contratos")
        .select(
            """
            id,
            org_id,
            deal_id,
            cota_id,
            numero,
            data_assinatura,
            status,
            pdf_path,
            pdf_filename,
            pdf_mime_type,
            pdf_size_bytes,
            pdf_uploaded_at,
            pdf_uploaded_by,
            pdf_uploaded_actor_type,
            pdf_version,
            pdf_status
            """
        )
        .eq("id", contract_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    data = _safe_data(resp)
    if not data:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    return data


def ensure_partner_can_access_contract(
    supa: Client,
    *,
    ctx: AuthContext,
    contract_id: str,
) -> None:
    if not ctx.is_partner:
        return

    if not ctx.can_view_contracts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Parceiro sem permissão para visualizar contratos",
        )

    if not ctx.parceiro_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Parceiro sem vínculo válido",
        )

    resp = (
        supa.table("contrato_parceiros")
        .select("id")
        .eq("org_id", ctx.org_id)
        .eq("contrato_id", contract_id)
        .eq("parceiro_id", ctx.parceiro_id)
        .maybe_single()
        .execute()
    )
    data = _safe_data(resp)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Parceiro sem acesso a este contrato",
        )


def ensure_can_read_contract_document(
    supa: Client,
    *,
    ctx: AuthContext,
    contract_id: str,
) -> None:
    if ctx.is_internal:
        return
    ensure_partner_can_access_contract(supa, ctx=ctx, contract_id=contract_id)


def ensure_can_upload_contract_document(
    *,
    ctx: AuthContext,
) -> None:
    if not ctx.is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas admin/gestor pode enviar contrato",
        )


def ensure_can_delete_contract_document(
    *,
    ctx: AuthContext,
) -> None:
    if not ctx.is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas admin/gestor pode remover contrato",
        )


def build_contract_pdf_path(org_id: str, contract_id: str) -> str:
    return f"orgs/{org_id}/contracts/{contract_id}/contrato.pdf"


def _looks_like_pdf(filename: Optional[str], content_type: Optional[str], content: bytes) -> bool:
    filename_ok = bool(filename and filename.lower().endswith(".pdf"))
    content_type_ok = content_type in ("application/pdf", "application/octet-stream", None)
    signature_ok = content[:4] == b"%PDF"
    return signature_ok and (filename_ok or content_type_ok)


async def read_and_validate_pdf(file: UploadFile) -> tuple[bytes, str]:
    content = await file.read()

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Arquivo vazio",
        )

    if len(content) > settings.CONTRACTS_MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Arquivo excede o limite de {settings.CONTRACTS_MAX_FILE_BYTES} bytes",
        )

    if not _looks_like_pdf(file.filename, file.content_type, content):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Apenas arquivos PDF são permitidos",
        )

    filename = file.filename or "contrato.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"

    return content, filename


def get_contract_document_metadata(
    supa: Client,
    *,
    ctx: AuthContext,
    contract_id: str,
) -> dict:
    ensure_can_read_contract_document(supa, ctx=ctx, contract_id=contract_id)
    contrato = get_contract_or_404(supa, org_id=ctx.org_id, contract_id=contract_id)

    return {
        "ok": True,
        "contract_id": contrato["id"],
        "has_document": bool(contrato.get("pdf_path")),
        "pdf_path": contrato.get("pdf_path"),
        "pdf_filename": contrato.get("pdf_filename"),
        "pdf_mime_type": contrato.get("pdf_mime_type"),
        "pdf_size_bytes": contrato.get("pdf_size_bytes"),
        "pdf_uploaded_at": contrato.get("pdf_uploaded_at"),
        "pdf_uploaded_by": contrato.get("pdf_uploaded_by"),
        "pdf_uploaded_actor_type": contrato.get("pdf_uploaded_actor_type"),
        "pdf_version": contrato.get("pdf_version"),
        "pdf_status": contrato.get("pdf_status"),
    }


async def upload_contract_document(
    supa: Client,
    *,
    ctx: AuthContext,
    contract_id: str,
    file: UploadFile,
) -> dict:
    ensure_can_upload_contract_document(ctx=ctx)

    contrato = get_contract_or_404(supa, org_id=ctx.org_id, contract_id=contract_id)
    content, filename = await read_and_validate_pdf(file)

    path = build_contract_pdf_path(ctx.org_id, contract_id)
    current_version = contrato.get("pdf_version") or 1
    next_version = current_version + 1 if contrato.get("pdf_path") else current_version

    try:
        supa.storage.from_(settings.CONTRACTS_BUCKET).upload(
            path,
            content,
            file_options={
                "content-type": "application/pdf",
                "upsert": "true",
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro no upload do contrato: {str(e)}",
        )

    update_payload = {
        "pdf_path": path,
        "pdf_filename": filename,
        "pdf_mime_type": "application/pdf",
        "pdf_size_bytes": len(content),
        "pdf_uploaded_at": utcnow_iso(),
        "pdf_uploaded_by": ctx.user_id,
        "pdf_uploaded_actor_type": "internal",
        "pdf_version": next_version,
        "pdf_status": "disponivel",
    }

    resp = (
        supa.table("contratos")
        .update(update_payload)
        .eq("id", contract_id)
        .eq("org_id", ctx.org_id)
        .execute()
    )
    data = _safe_data(resp) or []
    if not data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao atualizar metadados do contrato",
        )

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="contrato",
        entity_id=contract_id,
        action="contract_pdf_uploaded",
        diff={
            "pdf_path": path,
            "pdf_filename": filename,
            "pdf_size_bytes": len(content),
            "pdf_version": next_version,
        },
    )

    return {
        "ok": True,
        "contract_id": contract_id,
        "bucket": settings.CONTRACTS_BUCKET,
        "path": path,
        "pdf_filename": filename,
        "pdf_size_bytes": len(content),
        "pdf_status": "disponivel",
    }


def generate_contract_document_signed_url(
    supa: Client,
    *,
    ctx: AuthContext,
    contract_id: str,
    expires_in: Optional[int] = None,
) -> dict:
    ensure_can_read_contract_document(supa, ctx=ctx, contract_id=contract_id)
    contrato = get_contract_or_404(supa, org_id=ctx.org_id, contract_id=contract_id)

    if not contrato.get("pdf_path"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contrato sem PDF",
        )

    ttl = expires_in or settings.CONTRACTS_SIGNED_URL_EXPIRES_IN

    try:
        signed = supa.storage.from_(settings.CONTRACTS_BUCKET).create_signed_url(
            contrato["pdf_path"],
            ttl,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao gerar URL assinada: {str(e)}",
        )

    signed_url = signed.get("signedURL") or signed.get("signedUrl") or signed.get("signed_url")
    if not signed_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao gerar URL assinada",
        )

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="contrato",
        entity_id=contract_id,
        action="contract_pdf_signed_url_created",
        diff={
            "actor_type": ctx.actor_type,
            "expires_in": ttl,
        },
    )

    return {
        "ok": True,
        "contract_id": contract_id,
        "signed_url": signed_url,
        "expires_in": ttl,
    }


def delete_contract_document(
    supa: Client,
    *,
    ctx: AuthContext,
    contract_id: str,
) -> dict:
    ensure_can_delete_contract_document(ctx=ctx)
    contrato = get_contract_or_404(supa, org_id=ctx.org_id, contract_id=contract_id)

    if not contrato.get("pdf_path"):
        return {"ok": True, "contract_id": contract_id, "deleted": False}

    path = contrato["pdf_path"]

    try:
        supa.storage.from_(settings.CONTRACTS_BUCKET).remove([path])
    except Exception:
        # não falha se o arquivo já não existir, limpa metadado mesmo assim
        pass

    update_payload = {
        "pdf_path": None,
        "pdf_filename": None,
        "pdf_mime_type": None,
        "pdf_size_bytes": None,
        "pdf_uploaded_at": None,
        "pdf_uploaded_by": None,
        "pdf_uploaded_actor_type": None,
        "pdf_status": "pendente",
    }

    resp = (
        supa.table("contratos")
        .update(update_payload)
        .eq("id", contract_id)
        .eq("org_id", ctx.org_id)
        .execute()
    )
    data = _safe_data(resp) or []
    if not data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao limpar metadados do contrato",
        )

    insert_audit_log(
        supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="contrato",
        entity_id=contract_id,
        action="contract_pdf_deleted",
        diff={"pdf_path": path},
    )

    return {"ok": True, "contract_id": contract_id, "deleted": True}