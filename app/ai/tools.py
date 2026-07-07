"""Ferramentas do agente de IA do WhatsApp.

Cada ferramenta roda escopada por `org_id` (isolamento multi-tenant). O agente as
chama; a execução acontece aqui, no backend. Nenhuma ferramenta inventa dado: a
simulação usa a mecânica real do consórcio (porte do simulador do sistema).
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import uuid4

from supabase import Client

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Simulador de consórcio (porte de front/.../simuladores/lib/consorcio.ts)
# --------------------------------------------------------------------------- #
_PRODUTOS = {
    "imovel": {"embutido_max": 0.30, "taxa_adesao": 0.02, "taxa_admin": 0.155, "fundo_reserva": 0.02, "seguro": 0.00038, "tem_adesao": True, "prazo": 200},
    "auto": {"embutido_max": 0.20, "taxa_adesao": 0.0, "taxa_admin": 0.18, "fundo_reserva": 0.02, "seguro": 0.00038, "tem_adesao": False, "prazo": 69},
    "pesados": {"embutido_max": 0.30, "taxa_adesao": 0.0, "taxa_admin": 0.14, "fundo_reserva": 0.02, "seguro": 0.00038, "tem_adesao": False, "prazo": 95},
}


def simular_consorcio(
    *,
    produto: str,
    valor_credito: float,
    prazo: Optional[int] = None,
    redutor_percentual: Optional[float] = None,
    lance_percentual: Optional[float] = None,
) -> dict[str, Any]:
    """Simulação de referência. Valores exatos dependem do grupo/administradora."""
    p = _PRODUTOS.get((produto or "").lower())
    if not p:
        return {"erro": "produto inválido", "produtos_validos": list(_PRODUTOS.keys())}
    credito = float(valor_credito or 0)
    if credito <= 0:
        return {"erro": "valor_credito deve ser maior que zero"}
    prazo = int(prazo or p["prazo"])
    redutor = (redutor_percentual or 0) / 100.0

    saldo_devedor = credito * (1 + p["taxa_admin"] + p["fundo_reserva"])
    adesao_pct = p["taxa_adesao"] if p["tem_adesao"] else 0.0
    categoria = credito * (1 + adesao_pct + p["taxa_admin"] + p["fundo_reserva"])
    seguro_mensal = saldo_devedor * p["seguro"]

    parcela_pj = saldo_devedor / prazo if prazo else 0
    parcela_integral = parcela_pj + seguro_mensal  # PF (com seguro)
    parcela_reduzida = parcela_pj * (1 - redutor) + seguro_mensal if redutor > 0 else parcela_integral

    resultado: dict[str, Any] = {
        "produto": produto,
        "valor_credito": round(credito, 2),
        "prazo_meses": prazo,
        "saldo_devedor_total": round(saldo_devedor, 2),
        "parcela_integral": round(parcela_integral, 2),
        "parcela_reduzida": round(parcela_reduzida, 2) if redutor > 0 else None,
        "taxa_administracao_pct": round(p["taxa_admin"] * 100, 2),
        "embutido_maximo_pct": round(p["embutido_max"] * 100, 2),
        "observacao": "Valores de referência. Taxas, prazos e regras de lance dependem da administradora e do grupo.",
    }
    if lance_percentual:
        resultado["lance_estimado"] = round(categoria * (lance_percentual / 100.0), 2)
        resultado["lance_percentual"] = lance_percentual
    return resultado


# --------------------------------------------------------------------------- #
# CRM: qualificação e dados do lead
# --------------------------------------------------------------------------- #
def registrar_qualificacao(
    *,
    supa: Client,
    org_id: str,
    lead_id: str,
    nome: Optional[str] = None,
    email: Optional[str] = None,
    produto_interesse: Optional[str] = None,
    objetivo: Optional[str] = None,
    valor_pretendido: Optional[float] = None,
    prazo_desejado: Optional[str] = None,
    parcela_confortavel: Optional[str] = None,
    intencao_lance: Optional[str] = None,
    perfil: Optional[str] = None,
    temperatura: Optional[str] = None,
    resumo: Optional[str] = None,
) -> dict[str, Any]:
    """Salva a qualificação no lead (dados básicos + interesse + atividade de resumo)."""
    try:
        lead_update: dict[str, Any] = {}
        if nome:
            lead_update["nome"] = nome
        if email:
            lead_update["email"] = email
        if lead_update:
            supa.table("leads").update(lead_update).eq("org_id", org_id).eq("id", lead_id).execute()

        # interesse (upsert simples: pega o mais recente, senão insere)
        interesse_obs = " | ".join(
            [x for x in [
                f"valor pretendido: {valor_pretendido}" if valor_pretendido else None,
                f"prazo: {prazo_desejado}" if prazo_desejado else None,
                f"parcela: {parcela_confortavel}" if parcela_confortavel else None,
                f"lance: {intencao_lance}" if intencao_lance else None,
                f"temperatura: {temperatura}" if temperatura else None,
            ] if x]
        )
        supa.table("lead_interesses").insert(
            {
                "org_id": org_id,
                "lead_id": lead_id,
                "produto": produto_interesse,
                "perfil_desejado": perfil,
                "objetivo": objetivo,
                "observacao": interesse_obs or None,
            }
        ).execute()

        if resumo:
            supa.table("activities").insert(
                {
                    "id": str(uuid4()),
                    "org_id": org_id,
                    "lead_id": lead_id,
                    "tipo": "whatsapp",
                    "assunto": "Qualificação pela IA (WhatsApp)",
                    "conteudo": resumo,
                }
            ).execute()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ia_registrar_qualificacao_falhou", extra={"org_id": org_id, "error": str(exc)})
        return {"ok": False, "erro": str(exc)}


# Etapas que a IA pode setar pela conversa do WhatsApp. Etapas de fechamento
# (contrato/ativo/pos_venda) e 'perdido' ficam fora: exigem ação humana.
_ETAPAS_IA = {
    "novo",
    "tentativa_contato",
    "contato_realizado",
    "diagnostico",
    "proposta",
    "negociacao",
    "frio",
}
_TEMPERATURAS = {"frio", "morno", "quente"}


def atualizar_etapa_classificacao(
    *,
    supa: Client,
    org_id: str,
    lead_id: str,
    etapa: Optional[str] = None,
    temperatura: Optional[str] = None,
    valor_agregado: Optional[float] = None,
    motivo: Optional[str] = None,
) -> dict[str, Any]:
    """Move o lead no funil e/ou classifica temperatura conforme a conversa evolui."""
    try:
        update: dict[str, Any] = {}
        etapa_norm = (etapa or "").strip().lower()
        if etapa_norm:
            if etapa_norm not in _ETAPAS_IA:
                return {"ok": False, "erro": f"etapa inválida para a IA: {etapa_norm}", "etapas_validas": sorted(_ETAPAS_IA)}
            update["etapa"] = etapa_norm

        temp_norm = (temperatura or "").strip().lower()
        if temp_norm:
            if temp_norm not in _TEMPERATURAS:
                return {"ok": False, "erro": f"temperatura inválida: {temp_norm}", "validas": sorted(_TEMPERATURAS)}
            update["temperatura"] = temp_norm
            update["temperatura_at"] = "now()"

        if valor_agregado and float(valor_agregado) > 0:
            update["valor_interesse"] = float(valor_agregado)

        if not update:
            return {"ok": False, "erro": "nada para atualizar"}

        # 'now()' precisa ir como expressão do Postgres; PostgREST aceita string ISO,
        # então usamos timestamp do lado do banco via update simples.
        if update.get("temperatura_at") == "now()":
            from datetime import datetime, timezone

            update["temperatura_at"] = datetime.now(timezone.utc).isoformat()

        supa.table("leads").update(update).eq("org_id", org_id).eq("id", lead_id).execute()

        supa.table("activities").insert(
            {
                "id": str(uuid4()),
                "org_id": org_id,
                "lead_id": lead_id,
                "tipo": "whatsapp",
                "assunto": "Atualização de funil pela IA (WhatsApp)",
                "conteudo": " | ".join(
                    [x for x in [
                        f"etapa: {etapa_norm}" if etapa_norm else None,
                        f"temperatura: {temp_norm}" if temp_norm else None,
                        f"valor: {valor_agregado}" if valor_agregado else None,
                        f"motivo: {motivo}" if motivo else None,
                    ] if x]
                ) or "atualização",
            }
        ).execute()
        return {"ok": True, "atualizado": {k: v for k, v in update.items() if k != "temperatura_at"}}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ia_atualizar_etapa_falhou", extra={"org_id": org_id, "error": str(exc)})
        return {"ok": False, "erro": str(exc)}


def buscar_dados_lead(*, supa: Client, org_id: str, lead_id: str) -> dict[str, Any]:
    """Contexto do lead: nome/telefone + último interesse."""
    try:
        lead_resp = (
            supa.table("leads").select("nome, telefone, email, etapa").eq("org_id", org_id).eq("id", lead_id).limit(1).execute()
        )
        lead_rows = getattr(lead_resp, "data", None) or []
        interesse_resp = (
            supa.table("lead_interesses")
            .select("produto, objetivo, perfil_desejado, observacao")
            .eq("org_id", org_id)
            .eq("lead_id", lead_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        interesse_rows = getattr(interesse_resp, "data", None) or []
        return {"lead": lead_rows[0] if lead_rows else {}, "interesse": interesse_rows[0] if interesse_rows else {}}
    except Exception as exc:  # noqa: BLE001
        return {"erro": str(exc)}


def listar_administradoras(*, supa: Client, org_id: str) -> list[str]:
    """Administradoras disponíveis para a org (globais + da org). Para a IA não inventar."""
    try:
        resp = (
            supa.table("administradoras")
            .select("nome, org_id")
            .or_(f"org_id.eq.{org_id},org_id.is.null")
            .order("nome")
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return [r["nome"] for r in rows if r.get("nome")]
    except Exception:  # noqa: BLE001
        return []
