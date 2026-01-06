# app/services/marketing_guide_service.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from supabase import Client


def _safe_upsert_lead(
    supa: Client,
    org_id: str,
    telefone: str,
    payload_full: Dict[str, Any],
    payload_min: Dict[str, Any],
) -> str:
    """
    Upsert simples por (org_id, telefone).
    - Se existir, update.
    - Se não existir, insert.
    Fallback: se payload_full falhar por colunas inexistentes, tenta payload_min.
    """
    existing = (
        supa.table("leads")
        .select("id, owner_id")
        .eq("org_id", org_id)
        .eq("telefone", telefone)
        .limit(1)
        .execute()
    ).data or []

    if existing:
        lead_id = existing[0]["id"]

        # regra: não sobrescrever owner_id se já existir
        if existing[0].get("owner_id"):
            payload_full.pop("owner_id", None)
            payload_min.pop("owner_id", None)

        # tenta update full, fallback min
        try:
            supa.table("leads").update(payload_full).eq("id", lead_id).execute()
        except Exception:
            supa.table("leads").update(payload_min).eq("id", lead_id).execute()

        return lead_id

    # insert
    try:
        inserted = supa.table("leads").insert(payload_full).execute().data or []
    except Exception:
        inserted = supa.table("leads").insert(payload_min).execute().data or []

    if not inserted:
        raise RuntimeError("Falha ao inserir lead (retorno vazio).")

    return inserted[0]["id"]


def _safe_insert_consent(
    supa: Client,
    consent_full: Dict[str, Any],
    consent_min: Dict[str, Any],
) -> None:
    """
    Insere consent log (imutável). Fallback para payload mínimo caso schema não tenha
    algumas colunas (ex.: org_id, ip, user_agent).
    """
    try:
        supa.table("consent_logs").insert(consent_full).execute()
    except Exception:
        supa.table("consent_logs").insert(consent_min).execute()


def resolve_landing_context(
    supa: Client,
    *,
    landing_slug: Optional[str],
    landing_hash: Optional[str],
) -> Tuple[str, str, str]:
    """
    Resolve dinamicamente o contexto da landing a partir da tabela landing_pages.

    Regras:
    - Deve existir landing_hash (public_hash) OU landing_slug (slug).
    - landing deve estar active = true.
    - Retorna: (org_id, owner_user_id, landing_id)

    Tabela esperada (conforme seu print):
      landing_pages: id, org_id, owner_user_id, slug, public_hash, active
    """
    if not landing_hash and not landing_slug:
        raise ValueError("landing_hash (public_hash) ou landing_slug (slug) é obrigatório.")

    q = (
        supa.table("landing_pages")
        .select("id, org_id, owner_user_id, active")
        .eq("active", True)
        .limit(1)
    )

    if landing_hash:
        q = q.eq("public_hash", landing_hash)
    else:
        q = q.eq("slug", landing_slug)

    data = q.execute().data or []
    if not data:
        raise LookupError("Landing não encontrada ou inativa.")

    row = data[0]
    org_id = row["org_id"]
    owner_id = row["owner_user_id"]
    landing_id = row["id"]

    return org_id, owner_id, landing_id


def submit_guide_lead(
    supa: Client,
    *,
    landing_slug: Optional[str],
    landing_hash: Optional[str],
    nome: str,
    telefone: str,
    email: Optional[str],
    consent_scope: str,
    utm: Dict[str, Optional[str]],
    referrer_url: Optional[str],
    user_agent: Optional[str],
    ip: Optional[str],
) -> str:
    """
    Cria/atualiza um LEAD (visitante da landing), vinculado ao dono da landing (owner_user_id)
    e à organização (org_id). Também registra consentimento (LGPD).

    Observação: visitante é LEAD, não é usuário autenticado.
    """
    org_id, owner_id, landing_id = resolve_landing_context(
        supa=supa,
        landing_slug=landing_slug,
        landing_hash=landing_hash,
    )

    # payload "full" (tenta gravar marketing)
    payload_full: Dict[str, Any] = {
        "org_id": org_id,
        "owner_id": owner_id,
        "landing_id": landing_id,
        "nome": nome,
        "telefone": telefone,
        "email": email,
        "origem": "lp",
        "etapa": "novo",
        "referrer_url": referrer_url,
        "user_agent": user_agent,
        "utm_source": utm.get("utm_source"),
        "utm_medium": utm.get("utm_medium"),
        "utm_campaign": utm.get("utm_campaign"),
        "utm_term": utm.get("utm_term"),
        "utm_content": utm.get("utm_content"),
    }

    # payload mínimo (caso o schema não tenha campos de marketing extras)
    payload_min: Dict[str, Any] = {
        "org_id": org_id,
        "owner_id": owner_id,
        "landing_id": landing_id,
        "nome": nome,
        "telefone": telefone,
        "email": email,
        "origem": "lp",
        "etapa": "novo",
    }

    lead_id = _safe_upsert_lead(
        supa=supa,
        org_id=org_id,
        telefone=telefone,
        payload_full=payload_full,
        payload_min=payload_min,
    )

    # consent log (imutável)
    consent_full = {
        "org_id": org_id,
        "lead_id": lead_id,
        "consentimento": True,
        "scope": consent_scope,
        "ip": ip,
        "user_agent": user_agent,
    }
    consent_min = {
        "lead_id": lead_id,
        "consentimento": True,
        "scope": consent_scope,
    }

    _safe_insert_consent(supa, consent_full=consent_full, consent_min=consent_min)

    return lead_id


def generate_guide_signed_url(
    supa: Client,
    *,
    lead_id: str,
    bucket: str,
    path_template: str,
    expires_in: int = 300,
) -> str:
    """
    Gera signed URL somente se houver consentimento.
    path_template suporta {org_id}.

    Observação: org_id é derivado do lead para manter isolamento multi-tenant.
    """
    # valida consentimento
    consent = (
        supa.table("consent_logs")
        .select("id")
        .eq("lead_id", lead_id)
        .eq("consentimento", True)
        .limit(1)
        .execute()
    ).data or []

    if not consent:
        raise PermissionError("Lead sem consentimento para baixar o material.")

    # busca org_id no lead
    lead = (
        supa.table("leads")
        .select("org_id")
        .eq("id", lead_id)
        .limit(1)
        .execute()
    ).data or []

    if not lead:
        raise LookupError("Lead não encontrado.")

    org_id = lead[0]["org_id"]
    path = path_template.format(org_id=org_id)

    signed = supa.storage.from_(bucket).create_signed_url(path, expires_in)

    url = signed.get("signedURL") or signed.get("signedUrl")
    if not url:
        raise RuntimeError("Falha ao gerar signed URL.")

    return url
