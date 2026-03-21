# app/schemas/contract_documents.py
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ContractSignedUrlIn(BaseModel):
    expires_in: Optional[int] = Field(default=None, ge=60, le=3600)


class ContractDocumentOut(BaseModel):
    ok: bool
    contract_id: str
    has_document: bool
    pdf_path: Optional[str] = None
    pdf_filename: Optional[str] = None
    pdf_mime_type: Optional[str] = None
    pdf_size_bytes: Optional[int] = None
    pdf_uploaded_at: Optional[str] = None
    pdf_uploaded_by: Optional[str] = None
    pdf_uploaded_actor_type: Optional[str] = None
    pdf_version: Optional[int] = None
    pdf_status: Optional[str] = None


class ContractSignedUrlOut(BaseModel):
    ok: bool
    contract_id: str
    signed_url: str
    expires_in: int


class ContractUploadOut(BaseModel):
    ok: bool
    contract_id: str
    bucket: str
    path: str
    pdf_filename: str
    pdf_size_bytes: int
    pdf_status: str


class ContractDeleteOut(BaseModel):
    ok: bool
    contract_id: str
    deleted: bool