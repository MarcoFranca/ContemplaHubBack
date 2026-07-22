"""Follow-up automático + lembretes de reunião (varredura periódica).

Regras de compliance (WhatsApp Cloud API):
- Mensagem livre só é entregue dentro da janela de 24h desde a última mensagem
  RECEBIDA do cliente. Por isso:
    * follow-up só ocorre quando existe mensagem do cliente e a janela está aberta;
    * lembretes só são enviados se a janela ainda estiver aberta (fora dela exigiria
      template aprovado, que ainda não temos) e o restante é apenas registrado.
- Respeita opt-out (leads.nao_perturbe) e handoff humano.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from supabase import Client

from app.core.config import settings
from app.services import whatsapp_service as wa

logger = logging.getLogger(__name__)

_ETAPAS_TERMINAIS = {"perdido", "contrato", "ativo", "pos_venda"}
_STATUS_ATIVOS = ["agendado", "confirmado"]
# Free entry point (Click-to-WhatsApp): janela de atendimento de 72h.
_CTWA_WINDOW_HOURS = 72
_MAX_WINDOW_HOURS = 72

# Cadência de reengajamento (sem prometer link/Google Meet). {nome} = ", Fulano" ou "".
_FOLLOWUP_MSGS = {
    "consorcio": [
        "Oi{nome}! Passando para retomar seu atendimento. Seu interesse no consórcio é para comprar imóvel, quitar financiamento ou formar patrimônio?",
        "Com duas informações eu já consigo te dar um norte melhor: valor aproximado da carta e em quanto tempo gostaria de usar o crédito. Pode me contar?",
        "Se ainda fizer sentido avaliar o consórcio, posso organizar as informações e marcar uma conversa com o corretor. Quer continuar?",
    ],
    "seguro_azos": [
        "Oi{nome}! Podemos retomar sua análise de Seguro de Vida? Quero entender sua realidade para sugerir uma proteção que faça sentido, sem exageros.",
        "Para continuar de onde paramos, posso fazer uma pergunta curta por vez sobre família, renda e trabalho. Tudo bem para você?",
        "Se ainda fizer sentido, posso concluir sua cotação Azos e deixar as coberturas e capitais claros antes de encaminhar ao corretor. Quer continuar?",
    ],
}


def _ultimo_produto_falado(messages: list[dict[str, Any]]) -> str:
    """Deriva o produto do contexto mais recente, sem misturar jornadas."""
    for message in reversed(messages):
        payload = message.get("payload")
        if isinstance(payload, dict) and payload.get("product") in {"seguro_azos", "consorcio"}:
            return payload["product"]
        text = str(message.get("body") or "").lower()
        if any(term in text for term in ("seguro de vida", "azos", "apólice", "apolice", "cobertura")):
            return "seguro_azos"
        if any(term in text for term in ("consórcio", "consorcio", "carta de crédito", "carta de credito", "contemplação", "contemplacao")):
            return "consorcio"
    return "consorcio"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _nome_curto(nome: Optional[str]) -> str:
    n = (nome or "").strip().split()[0] if (nome or "").strip() else ""
    return f", {n}" if n else ""


def _digits(v: Optional[str]) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit())


def _tem_referral(row: dict[str, Any]) -> bool:
    """Mensagem veio de anúncio Click-to-WhatsApp (free entry point)?"""
    payload = row.get("payload")
    return isinstance(payload, dict) and bool(payload.get("referral"))


def _janela_horas(referral: bool) -> int:
    return _CTWA_WINDOW_HOURS if referral else settings.FOLLOWUP_WINDOW_HOURS


def _em_horario_de_silencio() -> bool:
    """Não incomodar de madrugada/noite (horário de Brasília, UTC-03:00)."""
    hora = _now().astimezone(timezone(timedelta(hours=-3))).hour
    start = settings.FOLLOWUP_QUIET_START_HOUR
    end = settings.FOLLOWUP_QUIET_END_HOUR
    return hora >= start or hora < end


def _active_integrations(supa: Client, *, exigir_ia: bool) -> list[dict[str, Any]]:
    q = supa.table("whatsapp_integrations").select("*").eq("ativo", True)
    if exigir_ia:
        q = q.eq("ai_enabled", True)
    return getattr(q.execute(), "data", None) or []


def _ultimo_inbound(supa: Client, org_id: str, lead_id: str) -> tuple[Optional[datetime], bool]:
    """(momento da última mensagem recebida, veio de anúncio CTWA?)."""
    resp = (
        supa.table("whatsapp_messages")
        .select("created_at, payload")
        .eq("org_id", org_id)
        .eq("lead_id", lead_id)
        .eq("direction", "in")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    if not rows:
        return None, False
    return _parse(rows[0]["created_at"]), _tem_referral(rows[0])


def _enviar_template(
    *, integ: dict[str, Any], supa: Client, org_id: str, lead_id: str, to: str, nome: Optional[str],
    template_name: str, lang: str, payload_extra: dict[str, Any],
) -> bool:
    """Envia um template aprovado (fora da janela) com 1 variável {{1}} = nome. Loga a saída."""
    token = (integ.get("access_token") or "").strip()
    phone_id = (integ.get("phone_number_id") or "").strip()
    if not token or not phone_id:
        return False
    body_param = (nome or "cliente").strip() or "cliente"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": body_param}]}],
        },
    }
    try:
        reply = wa.send_template_message(access_token=token, phone_number_id=phone_id, payload=payload)
        wamid = None
        msgs = reply.get("messages") if isinstance(reply, dict) else None
        if isinstance(msgs, list) and msgs:
            wamid = msgs[0].get("id")
        supa.table("whatsapp_messages").insert(
            {
                "org_id": org_id,
                "lead_id": lead_id,
                "direction": "out",
                "wa_message_id": wamid,
                "phone": to,
                "msg_type": "template",
                "body": f"[template] {template_name}",
                "status": "sent",
                "payload": {"ai": True, "template": template_name, **payload_extra},
            }
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("template_envio_falhou", extra={"org_id": org_id, "lead_id": lead_id, "error": str(exc)})
        return False


def run_sweeps(supa: Client, *, limit: int = 50) -> dict[str, int]:
    stats = {"followups": 0, "reminders": 0}
    if settings.FOLLOWUP_ENABLED and settings.WHATSAPP_AI_ENABLED:
        try:
            stats["followups"] = sweep_followups(supa, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("followup_sweep_erro", extra={"error": str(exc)})
    if settings.REMINDER_ENABLED:
        try:
            stats["reminders"] = sweep_reminders(supa, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reminder_sweep_erro", extra={"error": str(exc)})
    if settings.FOLLOWUP_ENABLED and settings.WHATSAPP_AI_ENABLED:
        try:
            stats["retomadas"] = sweep_retomadas(supa, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("retomada_sweep_erro", extra={"error": str(exc)})
    return stats


def sweep_retomadas(supa: Client, *, limit: int = 50) -> int:
    """Leads com data de retomada vencida: tenta reabrir o agendamento da reunião."""
    from app.ai import agent as ai_agent

    if _em_horario_de_silencio():
        return 0
    now = _now()
    sent = 0
    for integ in _active_integrations(supa, exigir_ia=True):
        if sent >= limit:
            break
        org_id = integ.get("org_id")
        try:
            leads = getattr(
                supa.table("leads")
                .select("id, nome, telefone, nao_perturbe")
                .eq("org_id", org_id)
                .lte("retomar_em", now.isoformat())
                .limit(limit)
                .execute(),
                "data",
                None,
            ) or []
        except Exception:  # noqa: BLE001
            leads = []

        for lead in leads:
            if sent >= limit:
                break
            lid = lead.get("id")
            if lead.get("nao_perturbe") or not lid:
                continue
            if ai_agent.lead_em_handoff(supa, org_id, lid):
                continue
            # já tem reunião ativa futura? então nada a retomar; limpa.
            ags = getattr(
                supa.table("agendamentos").select("id").eq("org_id", org_id).eq("lead_id", lid).in_("status", _STATUS_ATIVOS).gte("inicio", now.isoformat()).limit(1).execute(),
                "data",
                None,
            ) or []
            if ags:
                supa.table("leads").update({"retomar_em": None}).eq("org_id", org_id).eq("id", lid).execute()
                continue
            last_in, veio_de_anuncio = _ultimo_inbound(supa, org_id, lid)
            janela = timedelta(hours=_janela_horas(veio_de_anuncio))
            if not (last_in and now - last_in < janela):
                continue  # fora da janela: não dá pra mandar mensagem livre agora
            to = _digits(lead.get("telefone"))
            if not to:
                continue
            nome = _nome_curto(lead.get("nome"))
            body = f"Oi{nome}! Podemos retomar o agendamento da sua reunião com o especialista? Me diz um dia e horário que eu vejo a disponibilidade."
            try:
                wa._send_and_log_reply(
                    supa=supa, integration=integ, org_id=org_id, lead_id=lid, to=to, body=body,
                    payload={"ai": True, "retomada": True},
                )
                supa.table("leads").update({"retomar_em": None}).eq("org_id", org_id).eq("id", lid).execute()
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("retomada_envio_falhou", extra={"org_id": org_id, "lead_id": lid, "error": str(exc)})
    return sent


# --------------------------------------------------------------------------- #
# Follow-up
# --------------------------------------------------------------------------- #
def sweep_followups(supa: Client, *, limit: int = 50) -> int:
    from app.ai import agent as ai_agent

    if _em_horario_de_silencio():
        return 0  # respeita horário de silêncio (não manda follow-up de madrugada/noite)

    now = _now()
    gap = timedelta(hours=settings.FOLLOWUP_MIN_GAP_HOURS)
    max_att = settings.FOLLOWUP_MAX_ATTEMPTS
    sent = 0

    for integ in _active_integrations(supa, exigir_ia=True):
        if sent >= limit:
            break
        org_id = integ.get("org_id")
        since = (now - timedelta(hours=_MAX_WINDOW_HOURS + 1)).isoformat()
        msgs = getattr(
            supa.table("whatsapp_messages")
            .select("lead_id, direction, body, created_at, payload")
            .eq("org_id", org_id)
            .gte("created_at", since)
            .order("created_at", desc=False)
            .execute(),
            "data",
            None,
        ) or []

        by_lead: dict[str, list[dict[str, Any]]] = {}
        for m in msgs:
            lid = m.get("lead_id")
            if lid:
                by_lead.setdefault(lid, []).append(m)

        tem_template = bool(settings.FOLLOWUP_TEMPLATE_NAME.strip() or settings.FOLLOWUP_SEGURO_TEMPLATE_NAME.strip())
        candidatos: list[tuple[str, int, bool]] = []  # (lead_id, followup_count, janela_aberta)
        for lid, ms in by_lead.items():
            last = ms[-1]
            if last.get("direction") == "in":
                continue  # cliente falou por último: o agente responde, não é follow-up
            inbound_rows = [x for x in ms if x.get("direction") == "in" and _parse(x.get("created_at"))]
            if not inbound_rows:
                continue  # nunca houve mensagem do cliente: sem janela de atendimento
            last_in_row = max(inbound_rows, key=lambda x: x["created_at"])
            last_in = _parse(last_in_row["created_at"])
            janela = timedelta(hours=_janela_horas(_tem_referral(last_in_row)))  # 72h se veio de anúncio
            aberta = (now - last_in) < janela
            if not aberta and not tem_template:
                continue  # janela fechada e sem template aprovado: nada a fazer
            outs = [x for x in ms if x.get("direction") == "out" and (_parse(x["created_at"]) or now) > last_in]
            last_out = max((_parse(x["created_at"]) for x in outs if _parse(x["created_at"])), default=None)
            if not last_out or now - last_out < gap:
                continue  # cedo demais desde a última mensagem nossa
            fups = sum(1 for x in outs if isinstance(x.get("payload"), dict) and x["payload"].get("followup"))
            if fups >= max_att:
                continue
            candidatos.append((lid, fups, aberta))

        if not candidatos:
            continue

        ids = [c[0] for c in candidatos]
        leads = getattr(
            supa.table("leads").select("id, nome, telefone, etapa, nao_perturbe").eq("org_id", org_id).in_("id", ids).execute(),
            "data",
            None,
        ) or []
        lead_map = {l["id"]: l for l in leads}
        # leads com reunião futura: não incomodar
        ags = getattr(
            supa.table("agendamentos").select("lead_id, inicio, status").eq("org_id", org_id).in_("lead_id", ids).in_("status", _STATUS_ATIVOS).execute(),
            "data",
            None,
        ) or []
        com_reuniao = {a["lead_id"] for a in ags if (_parse(a["inicio"]) or now) > now}

        for lid, fups, aberta in candidatos:
            if sent >= limit:
                break
            lead = lead_map.get(lid)
            if not lead or lead.get("nao_perturbe"):
                continue
            if (lead.get("etapa") or "") in _ETAPAS_TERMINAIS:
                continue
            if lid in com_reuniao:
                continue
            if ai_agent.lead_em_handoff(supa, org_id, lid):
                continue
            to = _digits(lead.get("telefone"))
            if not to:
                continue
            nome = lead.get("nome")
            product = _ultimo_produto_falado(by_lead.get(lid) or [])
            if aberta:
                sequence = _FOLLOWUP_MSGS[product]
                body = sequence[min(fups, len(sequence) - 1)].format(nome=_nome_curto(nome))
                try:
                    wa._send_and_log_reply(
                        supa=supa,
                        integration=integ,
                        org_id=org_id,
                        lead_id=lid,
                        to=to,
                        body=body,
                        payload={"ai": True, "followup": True, "attempt": fups + 1, "product": product},
                    )
                    sent += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("followup_envio_falhou", extra={"org_id": org_id, "lead_id": lid, "error": str(exc)})
            else:
                # janela fechada: template aprovado (opt-in via env)
                template_name = (
                    settings.FOLLOWUP_SEGURO_TEMPLATE_NAME.strip()
                    if product == "seguro_azos"
                    else settings.FOLLOWUP_TEMPLATE_NAME.strip()
                )
                if not template_name:
                    continue
                ok = _enviar_template(
                    integ=integ, supa=supa, org_id=org_id, lead_id=lid, to=to, nome=nome,
                    template_name=template_name,
                    lang=settings.FOLLOWUP_TEMPLATE_LANG.strip() or "pt_BR",
                    payload_extra={"followup": True, "attempt": fups + 1, "product": product},
                )
                if ok:
                    sent += 1

    return sent


# --------------------------------------------------------------------------- #
# Lembretes de reunião
# --------------------------------------------------------------------------- #
def sweep_reminders(supa: Client, *, limit: int = 50) -> int:
    now = _now()
    sent = 0

    for integ in _active_integrations(supa, exigir_ia=False):
        if sent >= limit:
            break
        org_id = integ.get("org_id")
        ags = getattr(
            supa.table("agendamentos")
            .select("id, lead_id, inicio, titulo, status, lembrete_24h_at, lembrete_1h_at")
            .eq("org_id", org_id)
            .in_("status", _STATUS_ATIVOS)
            .gte("inicio", now.isoformat())
            .lte("inicio", (now + timedelta(hours=25)).isoformat())
            .execute(),
            "data",
            None,
        ) or []

        for ag in ags:
            if sent >= limit:
                break
            inicio = _parse(ag.get("inicio"))
            if not inicio:
                continue
            delta = inicio - now
            tipo: Optional[str] = None
            if ag.get("lembrete_24h_at") is None and timedelta(hours=2) < delta <= timedelta(hours=24):
                tipo = "24h"
            elif ag.get("lembrete_1h_at") is None and timedelta(0) < delta <= timedelta(minutes=90):
                tipo = "1h"
            if not tipo:
                continue

            lid = ag.get("lead_id")
            lead = None
            if lid:
                lr = getattr(
                    supa.table("leads").select("nome, telefone, nao_perturbe").eq("org_id", org_id).eq("id", lid).limit(1).execute(),
                    "data",
                    None,
                ) or []
                lead = lr[0] if lr else None
            if not lead or lead.get("nao_perturbe"):
                continue

            # janela de atendimento aberta? (fora dela, mensagem livre não entrega)
            last_in, veio_de_anuncio = _ultimo_inbound(supa, org_id, lid) if lid else (None, False)
            janela = timedelta(hours=_janela_horas(veio_de_anuncio))
            janela_aberta = bool(last_in and now - last_in < janela)
            marca = {"lembrete_24h_at" if tipo == "24h" else "lembrete_1h_at": now.isoformat()}
            to = _digits(lead.get("telefone"))
            rem_template = settings.REMINDER_TEMPLATE_NAME.strip()
            if not janela_aberta:
                # fora da janela: template aprovado (opt-in) ou apenas marca como processado.
                if rem_template and to:
                    ok = _enviar_template(
                        integ=integ, supa=supa, org_id=org_id, lead_id=lid, to=to, nome=lead.get("nome"),
                        template_name=rem_template,
                        lang=settings.REMINDER_TEMPLATE_LANG.strip() or "pt_BR",
                        payload_extra={"reminder": tipo},
                    )
                    if ok:
                        sent += 1
                else:
                    logger.info("reminder_fora_da_janela", extra={"org_id": org_id, "agendamento_id": ag["id"], "tipo": tipo})
                supa.table("agendamentos").update(marca).eq("org_id", org_id).eq("id", ag["id"]).execute()
                continue

            hora = inicio.astimezone(timezone(timedelta(hours=-3))).strftime("%H:%M")
            nome = _nome_curto(lead.get("nome"))
            if tipo == "24h":
                body = f"Olá{nome}! Passando para confirmar sua reunião sobre consórcio às {hora}. Está tudo certo para você?"
            else:
                body = f"{nome[2:] or 'Olá'}, sua reunião com o corretor especialista é hoje às {hora}. O corretor vai te enviar o link de acesso. Até já!"

            to = _digits(lead.get("telefone"))
            if not to:
                continue
            try:
                wa._send_and_log_reply(
                    supa=supa,
                    integration=integ,
                    org_id=org_id,
                    lead_id=lid,
                    to=to,
                    body=body,
                    payload={"ai": True, "reminder": tipo},
                )
                supa.table("agendamentos").update(marca).eq("org_id", org_id).eq("id", ag["id"]).execute()
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("reminder_envio_falhou", extra={"org_id": org_id, "agendamento_id": ag["id"], "error": str(exc)})

    return sent
