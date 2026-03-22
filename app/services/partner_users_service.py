from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException
from supabase import Client

from app.core.config import settings
from app.security.auth import AuthContext
from app.schemas.partner_users import (
    PartnerAccessToggleIn,
    PartnerUserInviteIn,
    PartnerUserResendInviteIn,
    PartnerUserUpdateIn,
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _safe_data(resp: Any) -> Any:
    return getattr(resp, "data", None)


def _safe_user(resp: Any) -> Any:
    return getattr(resp, "user", None)


def _dig(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            cur = getattr(cur, key, None)
    return cur


def get_org_record_or_404(
    supa: Client,
    table: str,
    org_id: str,
    record_id: str,
    columns: str = "*",
) -> Dict[str, Any]:
    resp = (
        supa.table(table)
        .select(columns)
        .eq("org_id", org_id)
        .eq("id", record_id)
        .maybe_single()
        .execute()
    )
    data = _safe_data(resp)
    if not data:
        raise HTTPException(404, f"Registro não encontrado em {table}")
    return data


def insert_audit_log(
    supa: Client,
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
        pass


def _extract_auth_user_from_invite_response(resp: Any) -> tuple[Optional[str], Optional[str]]:
    user = _safe_user(resp)
    if user:
        return getattr(user, "id", None), getattr(user, "email", None)

    data = _safe_data(resp)
    if isinstance(data, dict):
        user_dict = data.get("user")
        if isinstance(user_dict, dict):
            return user_dict.get("id"), user_dict.get("email")

    user_id = _dig(resp, "user", "id") or _dig(resp, "data", "user", "id")
    user_email = _dig(resp, "user", "email") or _dig(resp, "data", "user", "email")

    return user_id, user_email


def _send_supabase_invite(
    supa: Client,
    email: str,
    redirect_to: Optional[str],
    metadata: dict,
) -> tuple[Optional[str], Optional[str]]:
    try:
        resp = supa.auth.admin.invite_user_by_email(
            email,
            {
                "redirect_to": redirect_to,
                "data": metadata,
            },
        )
        return _extract_auth_user_from_invite_response(resp)
    except TypeError:
        try:
            resp = supa.auth.admin.invite_user_by_email(
                email=email,
                options={
                    "redirect_to": redirect_to,
                    "data": metadata,
                },
            )
            return _extract_auth_user_from_invite_response(resp)
        except TypeError:
            pass
    except Exception as e:
        raise HTTPException(400, f"Erro ao enviar convite do parceiro: {str(e)}")

    try:
        resp = supa.auth.admin.generate_link(
            {
                "type": "invite",
                "email": email,
                "options": {
                    "redirect_to": redirect_to,
                    "data": metadata,
                },
            }
        )
        return _extract_auth_user_from_invite_response(resp)
    except TypeError:
        try:
            resp = supa.auth.admin.generate_link(
                {
                    "type": "invite",
                    "email": email,
                    "redirect_to": redirect_to,
                    "data": metadata,
                }
            )
            return _extract_auth_user_from_invite_response(resp)
        except Exception as e:
            raise HTTPException(400, f"Erro ao gerar link de convite do parceiro: {str(e)}")
    except Exception as e:
        raise HTTPException(400, f"Erro ao gerar link de convite do parceiro: {str(e)}")


def _build_partner_user_payload(
    org_id: str,
    parceiro: dict,
    email: str,
    nome: Optional[str],
    telefone: Optional[str],
    ativo: bool,
    can_view_client_data: bool,
    can_view_contracts: bool,
    can_view_commissions: bool,
    auth_user_id: Optional[str] = None,
) -> dict:
    now_iso = utcnow_iso()

    payload = {
        "org_id": org_id,
        "parceiro_id": parceiro["id"],
        "auth_user_id": auth_user_id,
        "email": email,
        "nome": nome or parceiro.get("nome"),
        "telefone": telefone or parceiro.get("telefone"),
        "ativo": ativo,
        "can_view_client_data": can_view_client_data,
        "can_view_contracts": can_view_contracts,
        "can_view_commissions": can_view_commissions,
        "updated_at": now_iso,
    }

    if ativo:
        payload["access_enabled_at"] = now_iso
        payload["disabled_at"] = None
        payload["disabled_reason"] = None
    else:
        payload["disabled_at"] = now_iso

    return payload


def list_partner_users(
    supa: Client,
    org_id: str,
    ativos: Optional[bool] = None,
) -> dict:
    query = (
        supa.table("partner_users")
        .select(
            """
            *,
            parceiros_corretores (
                id,
                nome,
                email,
                telefone,
                ativo
            )
            """
        )
        .eq("org_id", org_id)
    )

    if ativos is not None:
        query = query.eq("ativo", ativos)

    resp = query.order("created_at", desc=True).execute()
    items = _safe_data(resp) or []
    return {"ok": True, "items": items}


def get_partner_user_detail(
    supa: Client,
    org_id: str,
    partner_user_id: str,
) -> dict:
    resp = (
        supa.table("partner_users")
        .select(
            """
            *,
            parceiros_corretores (
                id,
                nome,
                email,
                telefone,
                ativo
            )
            """
        )
        .eq("org_id", org_id)
        .eq("id", partner_user_id)
        .maybe_single()
        .execute()
    )
    data = _safe_data(resp)
    if not data:
        raise HTTPException(404, "Acesso do parceiro não encontrado")

    return {"ok": True, "item": data}


def ensure_partner_user_access(
    supa: Client,
    ctx: AuthContext,
    body: PartnerUserInviteIn,
) -> dict:
    if not ctx.is_manager:
        raise HTTPException(403, "Apenas admin/gestor pode criar acesso de parceiro")

    org_id = ctx.org_id
    email = normalize_email(str(body.email))

    parceiro = get_org_record_or_404(
        supa,
        "parceiros_corretores",
        org_id,
        body.parceiro_id,
        columns="id, org_id, nome, email, telefone, ativo",
    )

    existing_same_partner_resp = (
        supa.table("partner_users")
        .select("*")
        .eq("org_id", org_id)
        .eq("parceiro_id", body.parceiro_id)
        .maybe_single()
        .execute()
    )
    existing_same_partner = _safe_data(existing_same_partner_resp)

    existing_same_email_resp = (
        supa.table("partner_users")
        .select("*")
        .eq("org_id", org_id)
        .eq("email", email)
        .maybe_single()
        .execute()
    )
    existing_same_email = _safe_data(existing_same_email_resp)

    if existing_same_email and (
        not existing_same_partner or existing_same_email["id"] != existing_same_partner["id"]
    ):
        raise HTTPException(
            409,
            "Já existe um acesso de parceiro com este email nesta organização",
        )

    metadata = {
        "actor_type": "partner",
        "org_id": org_id,
        "parceiro_id": body.parceiro_id,
        "nome": body.nome or parceiro.get("nome"),
    }

    auth_user_id: Optional[str] = None
    auth_email: Optional[str] = None
    invite_sent_at: Optional[str] = None

    if body.ativo:
        auth_user_id, auth_email = _send_supabase_invite(
            supa=supa,
            email=email,
            redirect_to=settings.PARTNER_INVITE_REDIRECT_TO,
            metadata=metadata,
        )
        invite_sent_at = utcnow_iso()

    payload = _build_partner_user_payload(
        org_id=org_id,
        parceiro=parceiro,
        email=auth_email or email,
        nome=body.nome,
        telefone=body.telefone,
        ativo=body.ativo,
        can_view_client_data=body.can_view_client_data,
        can_view_contracts=body.can_view_contracts,
        can_view_commissions=body.can_view_commissions,
        auth_user_id=auth_user_id,
    )

    if invite_sent_at:
        payload["invited_at"] = invite_sent_at
        payload["invite_sent_at"] = invite_sent_at

    if existing_same_partner:
        resp = (
            supa.table("partner_users")
            .update(payload)
            .eq("org_id", org_id)
            .eq("id", existing_same_partner["id"])
            .execute()
        )
        data = _safe_data(resp) or []
        item = data[0] if data else existing_same_partner
        action = "partner_user_upserted_existing"
    else:
        payload["created_at"] = utcnow_iso()
        resp = supa.table("partner_users").insert(payload, returning="representation").execute()
        data = _safe_data(resp) or []
        item = data[0] if data else None
        action = "partner_user_created"

    if not item:
        raise HTTPException(500, "Falha ao salvar acesso do parceiro")

    partner_update_payload = {
        "updated_at": utcnow_iso(),
    }
    if body.telefone:
        partner_update_payload["telefone"] = body.telefone
    if body.nome:
        partner_update_payload["nome"] = body.nome
    if email:
        partner_update_payload["email"] = email

    try:
        (
            supa.table("parceiros_corretores")
            .update(partner_update_payload)
            .eq("org_id", org_id)
            .eq("id", body.parceiro_id)
            .execute()
        )
    except Exception:
        pass

    insert_audit_log(
        supa=supa,
        org_id=org_id,
        actor_id=ctx.user_id,
        entity="partner_user",
        entity_id=item["id"],
        action=action,
        diff={
            "parceiro_id": body.parceiro_id,
            "email": email,
            "ativo": body.ativo,
            "can_view_client_data": body.can_view_client_data,
            "can_view_contracts": body.can_view_contracts,
            "can_view_commissions": body.can_view_commissions,
        },
    )

    return {"ok": True, "item": item}


def invite_partner_user(
    supa: Client,
    ctx: AuthContext,
    body: PartnerUserInviteIn,
) -> dict:
    return ensure_partner_user_access(supa=supa, ctx=ctx, body=body)


def resend_partner_invite(
    supa: Client,
    ctx: AuthContext,
    partner_user_id: str,
    body: PartnerUserResendInviteIn,
) -> dict:
    if not ctx.is_manager:
        raise HTTPException(403, "Apenas admin/gestor pode reenviar convite")

    current = (
        supa.table("partner_users")
        .select("*")
        .eq("org_id", ctx.org_id)
        .eq("id", partner_user_id)
        .maybe_single()
        .execute()
    )
    item = _safe_data(current)
    if not item:
        raise HTTPException(404, "Acesso do parceiro não encontrado")

    parceiro = get_org_record_or_404(
        supa,
        "parceiros_corretores",
        ctx.org_id,
        item["parceiro_id"],
        columns="id, nome, email, telefone, ativo",
    )

    metadata = {
        "actor_type": "partner",
        "org_id": ctx.org_id,
        "parceiro_id": item["parceiro_id"],
        "nome": item.get("nome") or parceiro.get("nome"),
    }

    auth_user_id, auth_email = _send_supabase_invite(
        supa=supa,
        email=normalize_email(item["email"]),
        redirect_to=body.redirect_to or settings.PARTNER_INVITE_REDIRECT_TO,
        metadata=metadata,
    )

    now_iso = utcnow_iso()
    update_payload = {
        "invited_at": now_iso,
        "invite_sent_at": now_iso,
        "updated_at": now_iso,
        "ativo": True,
        "access_enabled_at": now_iso,
        "disabled_at": None,
        "disabled_reason": None,
    }
    if auth_user_id:
        update_payload["auth_user_id"] = auth_user_id
    if auth_email:
        update_payload["email"] = normalize_email(auth_email)

    resp = (
        supa.table("partner_users")
        .update(update_payload)
        .eq("org_id", ctx.org_id)
        .eq("id", partner_user_id)
        .execute()
    )
    data = _safe_data(resp) or []
    updated = data[0] if data else item

    insert_audit_log(
        supa=supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="partner_user",
        entity_id=partner_user_id,
        action="partner_user_invite_resent",
        diff={
            "email": item["email"],
            "redirect_to": body.redirect_to or settings.PARTNER_INVITE_REDIRECT_TO,
        },
    )

    return {"ok": True, "item": updated}


def update_partner_user(
    supa: Client,
    ctx: AuthContext,
    partner_user_id: str,
    body: PartnerUserUpdateIn,
) -> dict:
    if not ctx.is_manager:
        raise HTTPException(403, "Apenas admin/gestor pode atualizar acesso do parceiro")

    current_resp = (
        supa.table("partner_users")
        .select("*")
        .eq("org_id", ctx.org_id)
        .eq("id", partner_user_id)
        .maybe_single()
        .execute()
    )
    current = _safe_data(current_resp)
    if not current:
        raise HTTPException(404, "Acesso do parceiro não encontrado")

    payload = body.model_dump(exclude_none=True)
    payload["updated_at"] = utcnow_iso()

    if "ativo" in payload:
        if payload["ativo"] is True:
            payload["access_enabled_at"] = utcnow_iso()
            payload["disabled_at"] = None
            payload["disabled_reason"] = None
        else:
            payload["disabled_at"] = utcnow_iso()

    resp = (
        supa.table("partner_users")
        .update(payload)
        .eq("org_id", ctx.org_id)
        .eq("id", partner_user_id)
        .execute()
    )
    data = _safe_data(resp) or []
    item = data[0] if data else None

    if not item:
        raise HTTPException(500, "Falha ao atualizar acesso do parceiro")

    insert_audit_log(
        supa=supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="partner_user",
        entity_id=partner_user_id,
        action="partner_user_updated",
        diff=payload,
    )

    return {"ok": True, "item": item}


def sync_partner_access_status(
    supa: Client,
    ctx: AuthContext,
    parceiro_id: str,
    ativo: bool,
    disabled_reason: Optional[str] = None,
) -> dict:
    if not ctx.is_manager:
        raise HTTPException(403, "Apenas admin/gestor pode alterar status do parceiro")

    parceiro = get_org_record_or_404(
        supa,
        "parceiros_corretores",
        ctx.org_id,
        parceiro_id,
        columns="id, nome, ativo",
    )

    now_iso = utcnow_iso()

    partner_payload = {
        "ativo": ativo,
        "updated_at": now_iso,
    }

    partner_resp = (
        supa.table("parceiros_corretores")
        .update(partner_payload)
        .eq("org_id", ctx.org_id)
        .eq("id", parceiro_id)
        .execute()
    )
    partner_data = _safe_data(partner_resp) or []
    partner_item = partner_data[0] if partner_data else parceiro

    access_resp = (
        supa.table("partner_users")
        .select("*")
        .eq("org_id", ctx.org_id)
        .eq("parceiro_id", parceiro_id)
        .maybe_single()
        .execute()
    )
    access_item = _safe_data(access_resp)

    updated_access = None
    if access_item:
        access_payload = {
            "ativo": ativo,
            "updated_at": now_iso,
        }

        if ativo:
            access_payload["access_enabled_at"] = now_iso
            access_payload["disabled_at"] = None
            access_payload["disabled_reason"] = None
        else:
            access_payload["disabled_at"] = now_iso
            access_payload["disabled_reason"] = disabled_reason

        resp = (
            supa.table("partner_users")
            .update(access_payload)
            .eq("org_id", ctx.org_id)
            .eq("id", access_item["id"])
            .execute()
        )
        data = _safe_data(resp) or []
        updated_access = data[0] if data else access_item

    insert_audit_log(
        supa=supa,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        entity="parceiro",
        entity_id=parceiro_id,
        action="parceiro_status_updated",
        diff={
            "ativo": ativo,
            "disabled_reason": disabled_reason,
        },
    )

    return {
        "ok": True,
        "partner": partner_item,
        "partner_user": updated_access,
    }


def toggle_partner_user_access(
    supa: Client,
    ctx: AuthContext,
    partner_user_id: str,
    body: PartnerAccessToggleIn,
) -> dict:
    current_resp = (
        supa.table("partner_users")
        .select("*")
        .eq("org_id", ctx.org_id)
        .eq("id", partner_user_id)
        .maybe_single()
        .execute()
    )
    current = _safe_data(current_resp)
    if not current:
        raise HTTPException(404, "Acesso do parceiro não encontrado")

    return update_partner_user(
        supa=supa,
        ctx=ctx,
        partner_user_id=partner_user_id,
        body=PartnerUserUpdateIn(
            ativo=body.ativo,
            disabled_reason=body.disabled_reason,
        ),
    )