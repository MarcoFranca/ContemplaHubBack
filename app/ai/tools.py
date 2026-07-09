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
# Mecânica por produto (embutido, seguro, adesão, prazo). Taxa adm, redutor e FR
# vêm da CAMPANHA ativa (ou do padrão abaixo), não daqui.
_PRODUTOS = {
    "imovel": {"embutido_max": 0.30, "taxa_adesao": 0.02, "seguro": 0.00038, "tem_adesao": True, "prazo": 200},
    "auto": {"embutido_max": 0.20, "taxa_adesao": 0.0, "seguro": 0.00038, "tem_adesao": False, "prazo": 69},
    "pesados": {"embutido_max": 0.30, "taxa_adesao": 0.0, "seguro": 0.00038, "tem_adesao": False, "prazo": 95},
}

# Padrão usado quando não há campanha ativa cadastrada.
_CAMPANHA_PADRAO = {"nome": "Padrão", "taxa_admin": 0.20, "redutor": 0.30, "fundo_reserva": 0.02, "prazo": None, "embutido_max": None}


def _resolver_campanha(supa, org_id: Optional[str], produto: str) -> dict[str, Any]:
    """Campanha ativa (produto exato > geral) ou o padrão. Percentuais em fração (0-1)."""
    from datetime import date

    if not supa or not org_id:
        return dict(_CAMPANHA_PADRAO)
    try:
        rows = getattr(
            supa.table("campanhas").select("*").eq("org_id", org_id).eq("ativo", True).execute(), "data", None
        ) or []
    except Exception:  # noqa: BLE001
        return dict(_CAMPANHA_PADRAO)

    hoje = date.today()

    def vigente(c: dict[str, Any]) -> bool:
        vi, vf = c.get("vigencia_inicio"), c.get("vigencia_fim")
        try:
            if vi and date.fromisoformat(vi) > hoje:
                return False
            if vf and date.fromisoformat(vf) < hoje:
                return False
        except Exception:  # noqa: BLE001
            pass
        return True

    cands = [c for c in rows if vigente(c)]
    exato = [c for c in cands if (c.get("produto") or "").lower() == produto.lower()]
    geral = [c for c in cands if (c.get("produto") or "geral").lower() in ("geral", "")]
    chosen = (exato or geral or [None])[0]
    if not chosen:
        return dict(_CAMPANHA_PADRAO)
    return {
        "nome": chosen.get("nome") or "Campanha",
        "administradora": chosen.get("administradora_nome"),
        "taxa_admin": (chosen.get("taxa_admin_pct") if chosen.get("taxa_admin_pct") is not None else 20) / 100.0,
        "redutor": (chosen.get("redutor_pct") if chosen.get("redutor_pct") is not None else 0) / 100.0,
        "fundo_reserva": (chosen.get("fundo_reserva_pct") if chosen.get("fundo_reserva_pct") is not None else 2) / 100.0,
        "prazo": chosen.get("prazo_meses"),
        "embutido_max": (chosen.get("embutido_max_pct") / 100.0) if chosen.get("embutido_max_pct") is not None else None,
    }


def simular_consorcio(
    *,
    produto: str,
    valor_credito: Optional[float] = None,
    parcela_alvo: Optional[float] = None,
    prazo: Optional[int] = None,
    redutor_percentual: Optional[float] = None,
    lance_percentual: Optional[float] = None,
    supa=None,
    org_id: Optional[str] = None,
) -> dict[str, Any]:
    """Estimativa de consórcio com FOCO NO REDUTOR (parcela reduzida até a contemplação).

    Usa a campanha ativa da org (ou o padrão). Se receber `parcela_alvo` sem `valor_credito`,
    calcula o MAIOR crédito cuja parcela reduzida cabe nessa parcela. Sempre é estimativa.
    """
    p = _PRODUTOS.get((produto or "").lower())
    if not p:
        return {"erro": "produto inválido", "produtos_validos": list(_PRODUTOS.keys())}

    camp = _resolver_campanha(supa, org_id, produto)
    taxa_admin = camp["taxa_admin"]
    fundo = camp["fundo_reserva"]
    # redutor: usa o pedido explicitamente; senão o da campanha/padrão.
    redutor = (redutor_percentual / 100.0) if redutor_percentual is not None else camp["redutor"]
    prazo = int(prazo or camp.get("prazo") or p["prazo"])
    seguro = p["seguro"]
    K = 1 + taxa_admin + fundo  # fator de saldo devedor

    # Cálculo reverso: dado a parcela confortável, achar o maior crédito (com redutor).
    usou_reverso = False
    if (not valor_credito) and parcela_alvo and parcela_alvo > 0:
        fator = K * ((1 - redutor) / prazo + seguro)
        credito = (float(parcela_alvo) / fator) if fator > 0 else 0
        # arredonda para baixo em passos de R$ 5 mil (crédito comercial redondo)
        valor_credito = max(0, int(credito // 5000) * 5000)
        usou_reverso = True

    credito = float(valor_credito or 0)
    if credito <= 0:
        return {"erro": "informe valor_credito ou parcela_alvo maiores que zero"}

    saldo_devedor = credito * K
    adesao_pct = p["taxa_adesao"] if p["tem_adesao"] else 0.0
    categoria = credito * (1 + adesao_pct + taxa_admin + fundo)
    seguro_mensal = saldo_devedor * seguro
    parcela_pj = saldo_devedor / prazo if prazo else 0
    parcela_integral = parcela_pj + seguro_mensal
    parcela_reduzida = parcela_pj * (1 - redutor) + seguro_mensal if redutor > 0 else parcela_integral

    resultado: dict[str, Any] = {
        "produto": produto,
        "campanha": camp.get("nome"),
        "administradora": camp.get("administradora"),
        "valor_credito": round(credito, 2),
        "prazo_meses": prazo,
        "redutor_pct": round(redutor * 100, 2),
        "parcela_reduzida": round(parcela_reduzida, 2),  # DESTAQUE: parcela até a contemplação
        "parcela_integral_apos_contemplacao": round(parcela_integral, 2),
        "taxa_administracao_pct": round(taxa_admin * 100, 2),
        "fundo_reserva_pct": round(fundo * 100, 2),
        "embutido_maximo_pct": round((camp.get("embutido_max") or p["embutido_max"]) * 100, 2),
        "reverso_por_parcela": usou_reverso,
        "observacao": "ESTIMATIVA (não é proposta). A parcela reduzida vale até a contemplação e depois sobe. Valores finais só na reunião com o corretor.",
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
            valor_credito=float(c.get("valor_carta") or 0) or None,
            parcela_alvo=c.get("parcela_alvo"),
            prazo=c.get("prazo"),
            redutor_percentual=c.get("redutor_percent"),
            supa=supa,
            org_id=org_id,
        )
        if sim.get("erro"):
            return {"ok": False, "erro": f"cenário {i + 1}: {sim['erro']}"}
        red_pct = sim.get("redutor_pct") or 0
        inputs.append(
            CreateProposalScenarioInput(
                id=chr(ord("A") + i),
                titulo=c.get("titulo") or f"Carta {produto} {sim.get('valor_credito')}",
                produto=_PRODUTO_PROPOSTA.get(produto, "outro"),
                administradora=c.get("administradora") or sim.get("administradora"),
                valor_carta=float(sim.get("valor_credito") or 0),
                prazo_meses=int(sim.get("prazo_meses") or 0),
                com_redutor=bool(red_pct and red_pct > 0),
                redutor_percent=red_pct or None,
                parcela_cheia=sim.get("parcela_integral_apos_contemplacao"),
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


def listar_campanhas(*, supa: Client, org_id: str) -> dict[str, Any]:
    """Campanhas ativas da org para a IA usar na estimativa. Se vazio, usa o padrão."""
    try:
        rows = getattr(
            supa.table("campanhas")
            .select("nome, administradora_nome, produto, taxa_admin_pct, redutor_pct, fundo_reserva_pct, prazo_meses")
            .eq("org_id", org_id)
            .eq("ativo", True)
            .execute(),
            "data",
            None,
        ) or []
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        return {
            "campanhas": [],
            "padrao": {"taxa_admin_pct": 20, "redutor_pct": 30, "fundo_reserva_pct": 2},
            "observacao": "Sem campanha cadastrada: usando o padrão (taxa adm 20%, redutor 30%, FR 2%).",
        }
    return {"campanhas": rows}


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
