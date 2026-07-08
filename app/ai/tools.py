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
# Proposta: a IA monta e envia uma proposta com base na simulação
# --------------------------------------------------------------------------- #
# Simulador usa imovel/auto/pesados; a proposta usa imobiliario/auto/outro.
_PRODUTO_PROPOSTA = {"imovel": "imobiliario", "auto": "auto", "pesados": "outro"}


def gerar_proposta(
    *,
    supa: Client,
    org_id: str,
    lead_id: str,
    created_by: Optional[str] = None,
    titulo: Optional[str] = None,
    cenarios: Optional[list[dict[str, Any]]] = None,
    enviar: bool = True,
) -> dict[str, Any]:
    """Monta uma proposta (calculando os números via simulador) e a envia ao cliente.

    Cada cenário: {produto, valor_carta, prazo?, redutor_percent?, administradora?, titulo?}.
    Retorna o link público da proposta para a IA mandar ao cliente.
    """
    import os

    from app.schemas.propostas import CreateLeadProposalInput, CreateProposalScenarioInput
    from app.services.lead_propostas_service import create_lead_proposta

    cenarios = cenarios or []
    if not cenarios:
        return {"ok": False, "erro": "informe ao menos um cenário"}

    inputs: list[CreateProposalScenarioInput] = []
    for i, c in enumerate(cenarios):
        produto = (c.get("produto") or "imovel").lower()
        sim = simular_consorcio(
            produto=produto,
            valor_credito=float(c.get("valor_carta") or 0),
            prazo=c.get("prazo"),
            redutor_percentual=c.get("redutor_percent"),
        )
        if sim.get("erro"):
            return {"ok": False, "erro": f"cenário {i + 1}: {sim['erro']}"}
        redutor = c.get("redutor_percent")
        inputs.append(
            CreateProposalScenarioInput(
                id=chr(ord("A") + i),
                titulo=c.get("titulo") or f"Carta {produto} {sim.get('valor_credito')}",
                produto=_PRODUTO_PROPOSTA.get(produto, "outro"),
                administradora=c.get("administradora"),
                valor_carta=float(sim.get("valor_credito") or 0),
                prazo_meses=int(sim.get("prazo_meses") or 0),
                com_redutor=bool(redutor and redutor > 0),
                redutor_percent=redutor,
                parcela_cheia=sim.get("parcela_integral"),
                parcela_reduzida=sim.get("parcela_reduzida"),
                taxa_admin_anual=sim.get("taxa_administracao_pct"),
                permite_lance_embutido=True,
                lance_embutido_pct_max=sim.get("embutido_maximo_pct"),
                observacoes="Valores de referência. Condições finais dependem da administradora e do grupo.",
            )
        )

    try:
        data = CreateLeadProposalInput(
            titulo=titulo or "Proposta de consórcio",
            status="enviado" if enviar else "rascunho",
            cenarios=inputs,
        )
        rec = create_lead_proposta(org_id=org_id, lead_id=lead_id, created_by=created_by, data=data, supa=supa)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ia_gerar_proposta_falhou", extra={"org_id": org_id, "error": str(exc)})
        return {"ok": False, "erro": str(exc)}

    frontend = os.getenv("FRONTEND_APP_URL", "https://app.contemplahub.com").rstrip("/")
    link = f"{frontend}/propostas/{rec.public_hash}" if rec.public_hash else None

    # ao enviar proposta, move o lead para a etapa 'proposta'
    if enviar:
        try:
            supa.table("leads").update({"etapa": "proposta"}).eq("org_id", org_id).eq("id", lead_id).execute()
        except Exception:  # noqa: BLE001
            pass

    # renderiza a proposta em PDF e envia como documento no WhatsApp
    pdf_enviado = False
    if enviar and link:
        pdf_enviado = _enviar_proposta_pdf(supa=supa, org_id=org_id, lead_id=lead_id, link=link, titulo=data.titulo)

    try:
        supa.table("activities").insert(
            {
                "id": str(uuid4()),
                "org_id": org_id,
                "lead_id": lead_id,
                "tipo": "whatsapp",
                "assunto": "Proposta gerada e enviada pela IA" if enviar else "Proposta em rascunho (IA)",
                "conteudo": f"{data.titulo} | {len(inputs)} cenário(s) | link: {link or 'n/d'} | pdf: {'sim' if pdf_enviado else 'não'}",
            }
        ).execute()
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "proposta_id": rec.id,
        "link": link,
        "status": rec.status,
        "cenarios": len(inputs),
        "pdf_enviado": pdf_enviado,
    }


def _enviar_proposta_pdf(*, supa: Client, org_id: str, lead_id: str, link: str, titulo: str) -> bool:
    """Renderiza a proposta em PDF e envia como documento no WhatsApp. Best-effort."""
    from app.services import pdf_render
    from app.services import whatsapp_service as wa

    try:
        integration = wa.get_integration_row(supa=supa, org_id=org_id)
        if not integration or not integration.get("ativo"):
            return False
        access_token = (integration.get("access_token") or "").strip()
        phone_number_id = (integration.get("phone_number_id") or "").strip()
        if not access_token or not phone_number_id:
            return False

        lead_resp = supa.table("leads").select("telefone").eq("org_id", org_id).eq("id", lead_id).limit(1).execute()
        lead_rows = getattr(lead_resp, "data", None) or []
        to = (lead_rows[0].get("telefone") if lead_rows else None) or ""
        to = "".join(ch for ch in to if ch.isdigit())
        if not to:
            return False

        pdf = pdf_render.render_url_to_pdf(link)
        if not pdf:
            return False

        media_id = wa.upload_media(
            access_token=access_token,
            phone_number_id=phone_number_id,
            data=pdf,
            mime="application/pdf",
            filename="proposta.pdf",
        )
        if not media_id:
            return False

        reply = wa.send_document_message(
            access_token=access_token,
            phone_number_id=phone_number_id,
            to=to,
            media_id=media_id,
            filename="proposta.pdf",
            caption=titulo,
        )
        reply_wamid = None
        msgs = reply.get("messages") if isinstance(reply, dict) else None
        if isinstance(msgs, list) and msgs:
            reply_wamid = msgs[0].get("id")

        try:
            supa.table("whatsapp_messages").insert(
                {
                    "org_id": org_id,
                    "lead_id": lead_id,
                    "direction": "out",
                    "wa_message_id": reply_wamid,
                    "phone": to,
                    "msg_type": "document",
                    "body": f"[PDF] {titulo}",
                    "status": "sent",
                    "payload": {"ai": True, "document": True},
                }
            ).execute()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("ia_enviar_proposta_pdf_falhou", extra={"org_id": org_id, "error": str(exc)})
        return False


# --------------------------------------------------------------------------- #
# Agenda: a IA agenda uma reunião com o especialista (agenda interna)
# --------------------------------------------------------------------------- #
def listar_horarios_disponiveis(*, supa: Client, org_id: str, lead_id: str, max_slots: int = 8) -> dict[str, Any]:
    """Retorna os próximos horários livres da agenda do especialista do lead."""
    from app.services import agenda_service

    cal = agenda_service.resolver_calendario_para_lead(supa=supa, org_id=org_id, lead_id=lead_id)
    if not cal:
        return {"ok": False, "erro": "sem agenda configurada", "instrucao": "combine um horário e escale para humano"}
    slots = agenda_service.listar_slots(supa=supa, org_id=org_id, calendario=cal, max_slots=int(max_slots or 8))
    if not slots:
        return {"ok": True, "horarios": [], "instrucao": "nenhum horário livre no período; ofereça retorno depois ou escale"}
    return {"ok": True, "agenda": cal.get("nome"), "horarios": slots}


def agendar_reuniao(
    *,
    supa: Client,
    org_id: str,
    lead_id: str,
    inicio: str,
    duracao_min: Optional[int] = None,
    titulo: Optional[str] = None,
    observacao: Optional[str] = None,
) -> dict[str, Any]:
    """Cria um agendamento validando disponibilidade da agenda. `inicio` em ISO 8601."""
    from datetime import datetime, timedelta

    from app.services import agenda_service

    try:
        dt = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return {"ok": False, "erro": "data/hora inválida (use ISO 8601, ex.: 2026-07-10T15:00:00-03:00)"}

    cal = agenda_service.resolver_calendario_para_lead(supa=supa, org_id=org_id, lead_id=lead_id)
    if not cal:
        return {"ok": False, "erro": "sem agenda configurada", "instrucao": "combine um horário e escale para humano"}

    dur = int(duracao_min or cal.get("slot_min") or 30)
    fim = agenda_service._iso(dt) + timedelta(minutes=dur)
    especialista_id = cal.get("especialista_id")

    if not agenda_service.slot_disponivel(supa=supa, org_id=org_id, calendario=cal, inicio=dt, slot_min=dur):
        slots = agenda_service.listar_slots(supa=supa, org_id=org_id, calendario=cal, max_slots=6)
        return {"ok": False, "erro": "horário indisponível", "horarios": slots, "instrucao": "ofereça um dos horários livres"}

    try:
        ins = (
            supa.table("agendamentos")
            .insert(
                {
                    "org_id": org_id,
                    "lead_id": lead_id,
                    "calendario_id": cal.get("id"),
                    "especialista_id": especialista_id,
                    "titulo": titulo or "Reunião com especialista",
                    "inicio": agenda_service._iso(dt).isoformat(),
                    "fim": fim.isoformat(),
                    "status": "agendado",
                    "origem": "ia",
                    "observacao": observacao,
                }
            )
            .execute()
        )
        rows = getattr(ins, "data", None) or []
        ag_id = rows[0].get("id") if rows else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("ia_agendar_reuniao_falhou", extra={"org_id": org_id, "error": str(exc)})
        return {"ok": False, "erro": str(exc)}

    # atividade tipo reuniao aparece na timeline do lead e notifica o time
    try:
        supa.table("activities").insert(
            {
                "id": str(uuid4()),
                "org_id": org_id,
                "lead_id": lead_id,
                "tipo": "reuniao",
                "assunto": "Reunião agendada pela IA",
                "conteudo": f"{titulo or 'Reunião com especialista'} em {dt.isoformat()} ({dur} min). {observacao or ''}".strip(),
            }
        ).execute()
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "agendamento_id": ag_id, "inicio": dt.isoformat(), "duracao_min": dur}


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


def registrar_opt_out(*, supa: Client, org_id: str, lead_id: str, motivo: Optional[str] = None) -> dict[str, Any]:
    """Cliente pediu para não ser mais contatado: corta automação e encerra o comercial."""
    from datetime import datetime, timezone

    try:
        supa.table("leads").update(
            {"nao_perturbe": True, "nao_perturbe_at": datetime.now(timezone.utc).isoformat(), "etapa": "perdido"}
        ).eq("org_id", org_id).eq("id", lead_id).execute()
        supa.table("activities").insert(
            {
                "id": str(uuid4()),
                "org_id": org_id,
                "lead_id": lead_id,
                "tipo": "whatsapp",
                "assunto": "Cliente pediu para não ser mais contatado (opt-out)",
                "conteudo": motivo or "",
            }
        ).execute()
        return {
            "ok": True,
            "instrucao": "Encerre com educação, agradeça e NÃO continue conduzindo. Não faça novas perguntas.",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("ia_opt_out_falhou", extra={"org_id": org_id, "error": str(exc)})
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
