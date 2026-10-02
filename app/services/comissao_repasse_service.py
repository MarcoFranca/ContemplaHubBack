from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException
from supabase import Client

from app.services.contract_partner_sync_service import insert_audit_log


def _safe_rows(resp: Any):
    return getattr(resp, "data", None) or []


def get_lancamento_or_404(
    supa: Client,
    *,
    org_id: str,
    lancamento_id: str,
) -> Dict[str, Any]:
    resp = (
        supa.table("comissao_lancamentos")
        .select("*")
        .eq("org_id", org_id)
        .eq("id", lancamento_id)
        .limit(1)
        .execute()
    )
    rows = _safe_rows(resp)
    if not rows:
        raise HTTPException(404, "Lançamento não encontrado")
    return rows[0]


def marcar_repasse_pago(
    supa: Client,
    *,
    org_id: str,
    lancamento_id: str,
    actor_id: Optional[str] = None,
    pago_em: Optional[str] = None,
    observacoes: Optional[str] = None,
    forcar: bool = False,
) -> Dict[str, Any]:
    lanc = get_lancamento_or_404(supa, org_id=org_id, lancamento_id=lancamento_id)

    if lanc.get("beneficiario_tipo") != "parceiro":
        raise HTTPException(400, "Somente lançamento de parceiro pode receber repasse")

    if lanc.get("status") == "cancelado":
        raise HTTPException(400, "Lançamento cancelado não pode ser pago")
    # Normalmente o repasse só é liberado após a comissão ser recebida. Com forcar=True
    # (fluxo do parceiro, onde o cliente conta como pago por padrão), paga mesmo assim e
    # quita a comissão junto (ver abaixo).
    if not forcar and lanc.get("status") not in {"disponivel", "pago"}:
        raise HTTPException(409, "A comissão ainda não foi recebida; o repasse permanece bloqueado")
    if lanc.get("repasse_status") == "pago":
        return {"ok": True, "item": lanc, "already_paid": True}

    repasse_pago_em = pago_em or datetime.utcnow().isoformat()

    payload = {
        "repasse_status": "pago",
        "repasse_pago_em": repasse_pago_em,
        "repasse_observacoes": observacoes,
        "updated_at": datetime.utcnow().isoformat(),
    }
    # Pagar o repasse implica comissão liquidada: quita o lançamento junto.
    if lanc.get("status") != "pago":
        payload["status"] = "pago"
        payload["pago_em"] = lanc.get("pago_em") or repasse_pago_em

    updated = (
        supa.table("comissao_lancamentos")
        .update(payload)
        .eq("org_id", org_id)
        .eq("id", lancamento_id)
        .execute()
    )
    rows = _safe_rows(updated)
    result = rows[0] if rows else {**lanc, **payload}

    insert_audit_log(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        entity="comissao_lancamentos",
        entity_id=lancamento_id,
        action="marcar_repasse_pago",
        diff={
            "antes": {
                "repasse_status": lanc.get("repasse_status"),
                "repasse_pago_em": lanc.get("repasse_pago_em"),
            },
            "depois": {
                "repasse_status": "pago",
                "repasse_pago_em": repasse_pago_em,
                "repasse_observacoes": observacoes,
            },
        },
    )

    return {
        "ok": True,
        "item": result,
    }


def reverter_repasse_pago(
    supa: Client,
    *,
    org_id: str,
    lancamento_id: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Desfaz um repasse marcado como pago por engano: volta para pendente e devolve a
    comissão para 'disponivel' (recebida, a repassar). Permite depois pular o mês/corrigir."""
    lanc = get_lancamento_or_404(supa, org_id=org_id, lancamento_id=lancamento_id)

    if lanc.get("beneficiario_tipo") != "parceiro":
        raise HTTPException(400, "Somente lançamento de parceiro tem repasse")
    if lanc.get("repasse_status") != "pago":
        return {"ok": True, "item": lanc, "already": True}

    payload: Dict[str, Any] = {
        "repasse_status": "pendente",
        "repasse_pago_em": None,
        "updated_at": datetime.utcnow().isoformat(),
    }
    # Pagar o repasse havia quitado a comissão; ao desfazer, volta para 'disponivel'.
    if lanc.get("status") == "pago":
        payload["status"] = "disponivel"

    updated = (
        supa.table("comissao_lancamentos")
        .update(payload)
        .eq("org_id", org_id)
        .eq("id", lancamento_id)
        .execute()
    )
    rows = getattr(updated, "data", None) or []
    result = rows[0] if rows else {**lanc, **payload}

    insert_audit_log(
        supa,
        org_id=org_id,
        actor_id=actor_id,
        entity="comissao_lancamentos",
        entity_id=lancamento_id,
        action="reverter_repasse_pago",
        diff={"antes": {"repasse_status": "pago"}, "depois": {"repasse_status": "pendente"}},
    )

    return {"ok": True, "item": result}
