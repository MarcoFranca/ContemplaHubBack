from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, UploadFile, status
from supabase import Client

from app.core.config import settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_rows(resp: Any) -> List[Dict[str, Any]]:
    return getattr(resp, "data", None) or []


def _slug(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "_", norm).strip("_")


# Tipos aceitos para comprovante: PDF e imagens comuns.
_SIGNATURES = {
    b"%PDF": ("application/pdf", "pdf"),
    b"\xff\xd8\xff": ("image/jpeg", "jpg"),
    b"\x89PNG": ("image/png", "png"),
}


async def _read_and_validate_comprovante(file: UploadFile) -> tuple[bytes, str, str]:
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Arquivo vazio")
    if len(content) > settings.CONTRACTS_MAX_FILE_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Arquivo excede o limite de {settings.CONTRACTS_MAX_FILE_BYTES} bytes",
        )
    mime = None
    ext = None
    for sig, (m, e) in _SIGNATURES.items():
        if content[: len(sig)] == sig:
            mime, ext = m, e
            break
    if not mime:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Comprovante deve ser PDF ou imagem (JPG/PNG)")
    return content, mime, ext


def create_repasse_lote(
    supa: Client,
    *,
    org_id: str,
    parceiro_id: str,
    lancamento_ids: List[str],
    forma_pagamento: Optional[str],
    observacoes: Optional[str],
    actor_id: str,
) -> Dict[str, Any]:
    if not lancamento_ids:
        raise HTTPException(400, "Selecione ao menos um repasse para pagar.")

    # Busca os lançamentos elegíveis (do parceiro, repasse pendente, na org).
    resp = (
        supa.table("comissao_lancamentos")
        .select("id, valor_liquido, repasse_status, beneficiario_tipo, parceiro_id")
        .eq("org_id", org_id)
        .eq("parceiro_id", parceiro_id)
        .eq("beneficiario_tipo", "parceiro")
        .in_("id", lancamento_ids)
        .execute()
    )
    rows = _safe_rows(resp)
    elegiveis = [r for r in rows if r.get("repasse_status") == "pendente"]
    if not elegiveis:
        raise HTTPException(409, "Nenhum repasse pendente válido na seleção.")

    total = sum((Decimal(str(r.get("valor_liquido") or 0)) for r in elegiveis), Decimal("0"))
    pago_em = _now_iso()

    lote_resp = (
        supa.table("repasse_lotes")
        .insert(
            {
                "org_id": org_id,
                "parceiro_id": parceiro_id,
                "total": float(total),
                "quantidade": len(elegiveis),
                "forma_pagamento": (forma_pagamento or "").strip() or None,
                "observacoes": (observacoes or "").strip() or None,
                "pago_em": pago_em,
                "actor_id": actor_id,
            },
            returning="representation",
        )
        .execute()
    )
    lote_rows = _safe_rows(lote_resp)
    if not lote_rows:
        raise HTTPException(500, "Erro ao criar lote de repasse")
    lote = lote_rows[0]

    ids = [r["id"] for r in elegiveis]
    (
        supa.table("comissao_lancamentos")
        .update(
            {
                "repasse_status": "pago",
                "repasse_pago_em": pago_em,
                "repasse_lote_id": lote["id"],
                "updated_at": pago_em,
            }
        )
        .eq("org_id", org_id)
        .in_("id", ids)
        .execute()
    )

    return {"ok": True, "lote": lote, "repasses_pagos": len(ids)}


def _get_lote_or_404(supa: Client, org_id: str, lote_id: str) -> Dict[str, Any]:
    resp = (
        supa.table("repasse_lotes").select("*").eq("org_id", org_id).eq("id", lote_id).limit(1).execute()
    )
    rows = _safe_rows(resp)
    if not rows:
        raise HTTPException(404, "Lote de repasse não encontrado")
    return rows[0]


async def upload_repasse_comprovante(
    supa: Client, *, org_id: str, lote_id: str, file: UploadFile, actor_id: str
) -> Dict[str, Any]:
    lote = _get_lote_or_404(supa, org_id, lote_id)
    content, mime, ext = await _read_and_validate_comprovante(file)

    path = f"repasses/{org_id}/{lote_id}/comprovante.{ext}"
    nome = file.filename or f"comprovante.{ext}"
    base = _slug(nome.rsplit(".", 1)[0]) or "comprovante"
    download_name = f"{base}.{ext}"

    try:
        supa.storage.from_(settings.CONTRACTS_BUCKET).upload(
            path, content, file_options={"content-type": mime, "upsert": "true"}
        )
    except Exception as e:
        raise HTTPException(500, f"Erro no upload do comprovante: {str(e)}")

    (
        supa.table("repasse_lotes")
        .update(
            {
                "comprovante_path": path,
                "comprovante_filename": download_name,
                "comprovante_mime": mime,
            }
        )
        .eq("org_id", org_id)
        .eq("id", lote_id)
        .execute()
    )
    return {"ok": True, "lote_id": lote_id, "comprovante_filename": download_name}


def create_repasse_comprovante_signed_url(
    supa: Client, *, org_id: str, lote_id: str, expires_in: int = 600
) -> Dict[str, Any]:
    lote = _get_lote_or_404(supa, org_id, lote_id)
    if not lote.get("comprovante_path"):
        raise HTTPException(404, "Lote sem comprovante")
    signed = supa.storage.from_(settings.CONTRACTS_BUCKET).create_signed_url(
        lote["comprovante_path"], expires_in
    )
    url = signed.get("signedURL") or signed.get("signed_url") if isinstance(signed, dict) else None
    return {"ok": True, "url": url, "filename": lote.get("comprovante_filename")}


def list_repasse_lotes(supa: Client, org_id: str, parceiro_id: Optional[str] = None) -> List[Dict[str, Any]]:
    query = supa.table("repasse_lotes").select("*").eq("org_id", org_id)
    if parceiro_id:
        query = query.eq("parceiro_id", parceiro_id)
    return _safe_rows(query.order("created_at", desc=True).execute())
