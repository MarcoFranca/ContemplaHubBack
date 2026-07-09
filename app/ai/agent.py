"""Motor do agente de IA (WhatsApp) usando Claude + tool use.

Camadas:
- GLOBAL (imutável p/ orgs): base de conhecimento em `app/ai/knowledge/` + guardrails.
- POR ORG: dados da org (administradoras) e do lead, injetados em runtime.

O agente conduz o atendimento consultivo, qualifica, simula (ferramenta) e escala
para humano quando necessário. Nunca inventa taxas/administradoras (usa dados da org).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional
from uuid import uuid4

from supabase import Client

from app.core.config import settings
from app.ai import tools as ai_tools

logger = logging.getLogger(__name__)

_KNOWLEDGE_CACHE: Optional[str] = None
_KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "knowledge")
_CONTEXT_NOISE_PAYLOAD_FLAGS = {
    "auto_reply",
    "ai_fallback",
    "ai_media_fallback",
    "followup",
    "reminder",
}


def _load_knowledge() -> str:
    global _KNOWLEDGE_CACHE
    if _KNOWLEDGE_CACHE is not None:
        return _KNOWLEDGE_CACHE
    parts: list[str] = []
    try:
        for fname in sorted(os.listdir(_KNOWLEDGE_DIR)):
            if fname.endswith(".md") and fname != "README.md":
                with open(os.path.join(_KNOWLEDGE_DIR, fname), encoding="utf-8") as f:
                    parts.append(f"# Arquivo: {fname}\n\n{f.read()}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ia_knowledge_load_falhou", extra={"error": str(exc)})
    _KNOWLEDGE_CACHE = "\n\n---\n\n".join(parts)
    return _KNOWLEDGE_CACHE


# --------------------------------------------------------------------------- #
# Definição das ferramentas (schema para o Claude)
# --------------------------------------------------------------------------- #
_TOOLS = [
    {
        "name": "simular_consorcio",
        "description": "Gera uma simulação de referência de consórcio (parcela, saldo, lance estimado). Use quando o cliente informar produto e valor. Valores exatos dependem da administradora.",
        "input_schema": {
            "type": "object",
            "properties": {
                "produto": {"type": "string", "enum": ["imovel", "auto", "pesados"]},
                "valor_credito": {"type": "number", "description": "Valor da carta de crédito em reais"},
                "prazo": {"type": "integer", "description": "Prazo em meses (opcional)"},
                "redutor_percentual": {"type": "number", "description": "Percentual de redução da parcela, se houver (opcional)"},
                "lance_percentual": {"type": "number", "description": "Percentual de lance para estimar o valor (opcional)"},
            },
            "required": ["produto", "valor_credito"],
        },
    },
    {
        "name": "registrar_qualificacao",
        "description": "Salva no CRM os dados de qualificação do cliente. Chame assim que tiver informações relevantes (objetivo, valor, prazo, parcela, perfil, temperatura, resumo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "nome": {"type": "string"},
                "email": {"type": "string"},
                "produto_interesse": {"type": "string"},
                "objetivo": {"type": "string"},
                "valor_pretendido": {"type": "number"},
                "prazo_desejado": {"type": "string"},
                "parcela_confortavel": {"type": "string"},
                "intencao_lance": {"type": "string"},
                "perfil": {"type": "string"},
                "temperatura": {"type": "string", "enum": ["quente", "morno", "frio"]},
                "resumo": {"type": "string", "description": "Resumo curto da conversa para o time"},
            },
            "required": [],
        },
    },
    {
        "name": "gerar_proposta",
        "description": (
            "Monta e ENVIA uma proposta formal de consórcio ao cliente. Calcula os números via simulador, gera um "
            "link público E JÁ ENVIA o PDF da proposta como documento no WhatsApp automaticamente. Use quando o "
            "cliente demonstrar intenção real e você já tiver produto e valor de carta. Pode ter 1 a 3 cenários "
            "(ex.: com e sem redutor, ou valores diferentes). Depois de chamar, comente com o cliente de forma "
            "natural que enviou a proposta (o PDF vai junto) e mande também o link retornado. NÃO invente "
            "administradora/taxa específica: só informe a administradora se souber com certeza pelos dados da org."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "titulo": {"type": "string", "description": "Título da proposta (ex.: 'Consórcio imóvel R$ 300 mil')"},
                "cenarios": {
                    "type": "array",
                    "description": "1 a 3 cenários de carta",
                    "items": {
                        "type": "object",
                        "properties": {
                            "produto": {"type": "string", "enum": ["imovel", "auto", "pesados"]},
                            "valor_carta": {"type": "number"},
                            "prazo": {"type": "integer"},
                            "redutor_percent": {"type": "number", "description": "0-100, se houver redutor"},
                            "administradora": {"type": "string"},
                            "titulo": {"type": "string"},
                        },
                        "required": ["produto", "valor_carta"],
                    },
                },
            },
            "required": ["cenarios"],
        },
    },
    {
        "name": "listar_horarios_disponiveis",
        "description": (
            "Lista os próximos horários livres da agenda do especialista para oferecer ao cliente. Use ANTES de "
            "agendar: nunca invente horários, ofereça só os que esta ferramenta retornar. Depois que o cliente "
            "escolher um, chame agendar_reuniao com o 'inicio' correspondente."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "agendar_reuniao",
        "description": (
            "Agenda uma reunião do cliente com o especialista na agenda interna. Use quando o cliente aceitar "
            "conversar com um especialista e você tiver combinado data e horário específicos com ele. SEMPRE confirme "
            "o dia e a hora exatos com o cliente ANTES de chamar. Passe 'inicio' em ISO 8601 com fuso -03:00. Se o "
            "horário voltar indisponível, ofereça outro ao cliente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inicio": {"type": "string", "description": "Início em ISO 8601, ex.: 2026-07-10T15:00:00-03:00"},
                "duracao_min": {"type": "integer", "description": "Duração em minutos (default 30)"},
                "titulo": {"type": "string"},
                "observacao": {"type": "string", "description": "Contexto para o especialista"},
            },
            "required": ["inicio"],
        },
    },
    {
        "name": "atualizar_etapa_classificacao",
        "description": (
            "Move o lead no funil de vendas e/ou classifica temperatura conforme a conversa evolui. "
            "Chame sempre que houver progresso real: respondeu -> 'contato_realizado'; começou a qualificar "
            "(objetivo/valor/prazo) -> 'diagnostico'; recebeu simulação/proposta -> 'proposta'; negociando "
            "condições -> 'negociacao'; sumiu/sem interesse -> 'frio'. Classifique temperatura: 'quente' (pronto/urgente), "
            "'morno' (interessado, ainda avaliando), 'frio' (curioso/sem urgência). Use 'valor_agregado' para o "
            "valor de carta pretendido quando souber. NÃO use etapas de fechamento (contrato/pós-venda): isso é humano."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "etapa": {
                    "type": "string",
                    "enum": ["novo", "tentativa_contato", "contato_realizado", "diagnostico", "proposta", "negociacao", "frio"],
                },
                "temperatura": {"type": "string", "enum": ["quente", "morno", "frio"]},
                "valor_agregado": {"type": "number", "description": "Valor de carta pretendido em reais (opcional)"},
                "motivo": {"type": "string", "description": "Por que mudou (curto, para o time)"},
            },
            "required": [],
        },
    },
    {
        "name": "registrar_opt_out",
        "description": (
            "Registra que o cliente pediu para NÃO ser mais contatado (ex.: 'não quero mais', 'pare de me mandar "
            "mensagem', 'me remova'). Corta a automação e encerra o comercial. Depois de chamar, apenas agradeça e "
            "encerre com educação, sem novas perguntas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"motivo": {"type": "string", "description": "O que o cliente disse (curto)"}},
            "required": [],
        },
    },
    {
        "name": "buscar_dados_lead",
        "description": "Consulta os dados já salvos do lead (nome, telefone, interesse) para não repetir perguntas.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "escalar_humano",
        "description": "Transfere para um especialista humano. Use SOMENTE nos gatilhos reais: fechamento/contrato/boleto/pagamento; taxa, administradora, grupo ou prazo de contemplação específicos; FGTS, quitação de financiamento, construção/reforma, documentos; cliente insatisfeito/irritado; pedido explícito de humano; assunto fora de consórcio. NÃO use para objeções, dúvidas, comparações ou hesitação ('consórcio é ruim', 'vou pensar', 'achei caro') nem para PEDIDO DE PROPOSTA (proposta você mesmo gera com gerar_proposta) - isso você conduz.",
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "description": "Motivo do escalonamento"},
                "resumo": {"type": "string", "description": "Resumo completo para o especialista continuar"},
            },
            "required": ["motivo"],
        },
    },
]


def _agora_brasil() -> str:
    from datetime import datetime, timedelta, timezone

    dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    agora = datetime.now(timezone(timedelta(hours=-3)))  # America/Sao_Paulo (sem DST atual)
    return f"{dias[agora.weekday()]}, {agora.strftime('%d/%m/%Y %H:%M')} (horário de Brasília, UTC-03:00)"


def _dados_coletados(supa: Client, org_id: str, lead_id: Optional[str]) -> str:
    """Resumo dos dados JÁ salvos do lead, para injetar no prompt como memória."""
    if not lead_id:
        return ""
    try:
        lr = supa.table("leads").select("nome, etapa, temperatura, valor_interesse, prazo_meses").eq("org_id", org_id).eq("id", lead_id).limit(1).execute()
        lead = (getattr(lr, "data", None) or [{}])[0]
        ir = (
            supa.table("lead_interesses")
            .select("produto, objetivo, perfil_desejado, observacao")
            .eq("org_id", org_id).eq("lead_id", lead_id).order("created_at", desc=True).limit(1).execute()
        )
        interesse = (getattr(ir, "data", None) or [{}])[0]
    except Exception:  # noqa: BLE001
        return ""

    linhas: list[str] = []
    if lead.get("nome"):
        linhas.append(f"- Nome: {lead['nome']}")
    if interesse.get("objetivo"):
        linhas.append(f"- Objetivo: {interesse['objetivo']}")
    if interesse.get("produto"):
        linhas.append(f"- Produto: {interesse['produto']}")
    if lead.get("valor_interesse"):
        linhas.append(f"- Valor de carta pretendido: R$ {lead['valor_interesse']}")
    if lead.get("prazo_meses"):
        linhas.append(f"- Prazo (meses): {lead['prazo_meses']}")
    if interesse.get("perfil_desejado"):
        linhas.append(f"- Perfil: {interesse['perfil_desejado']}")
    if interesse.get("observacao"):
        linhas.append(f"- Observações: {interesse['observacao']}")
    if lead.get("temperatura"):
        linhas.append(f"- Temperatura: {lead['temperatura']}")
    if lead.get("etapa"):
        linhas.append(f"- Etapa atual no funil: {lead['etapa']}")
    return "\n".join(linhas)


def _build_system(*, org_administradoras: list[str], nome_cliente: Optional[str]) -> str:
    knowledge = _load_knowledge()
    admins = ", ".join(org_administradoras) if org_administradoras else "(nenhuma cadastrada; se perguntarem administradora específica, escale para humano)"
    cliente = f"O cliente se chama {nome_cliente}." if nome_cliente else "Você ainda não sabe o nome do cliente."
    return (
        "Você é o assistente virtual de atendimento de uma consultoria de consórcio, atendendo pelo WhatsApp.\n"
        "Use a base de conhecimento abaixo como guia principal de identidade, tom, objeções, FAQ, "
        "qualificação, processo de venda e compliance. As regras de compliance e o tom prevalecem sobre tudo.\n\n"
        "Regras operacionais adicionais:\n"
        "- Responda em pt-BR, mensagens curtas e naturais para WhatsApp. Sem travessão (—).\n"
        "- Responda primeiro ao que o cliente acabou de dizer. Só depois decida se vale perguntar, explicar, "
        "simular, propor reunião, gerar proposta ou apenas confirmar o entendimento.\n"
        "- Não repita a mesma pergunta. Se o cliente já respondeu, desconversou, não sabe ou ignorou, siga com o "
        "que já tem: faça uma pergunta diferente, dê uma referência inicial com ressalvas, proponha o próximo passo "
        "ou apenas avance a conversa sem insistir no mesmo ponto.\n"
        "- Não force CTA em toda mensagem. Em alguns turnos, a melhor resposta é só esclarecer, acolher, resumir ou "
        "confirmar. Conduza com naturalidade, sem parecer script.\n"
        "- Varie a forma de responder. Evite abrir sempre do mesmo jeito, evite repetir a mesma estrutura e prefira "
        "1 ou 2 parágrafos curtos. Quando uma resposta curta resolver, seja breve.\n"
        "- NUNCA invente taxas, administradoras, grupos, prazos ou percentuais. Use as ferramentas e os dados da org.\n"
        "- Use `simular_consorcio` para números; use `registrar_qualificacao` conforme for descobrindo dados.\n"
        "- Use `atualizar_etapa_classificacao` para mover o lead no funil e classificar a temperatura sempre que a "
        "conversa avançar (respondeu, começou a qualificar, recebeu proposta, negociando, esfriou). Isso mantém o "
        "kanban do time atualizado sozinho. Não anuncie isso ao cliente, faça em segundo plano.\n"
        "- Use `gerar_proposta` quando o cliente demonstrar intenção real e você já tiver produto e valor de carta: "
        "a ferramenta cria e envia a proposta e devolve um link; mande esse link ao cliente de forma natural. "
        "Pedir proposta NÃO é escalonamento: você mesmo gera.\n"
        "- Para agendar reunião com especialista: primeiro chame `listar_horarios_disponiveis` e ofereça ao cliente "
        "APENAS os horários retornados (nunca invente). Quando ele escolher, chame `agendar_reuniao` com o 'inicio' "
        f"daquele horário. A data/hora atual é {_agora_brasil()}. Confirme ao cliente o horário marcado. Isso "
        "substitui o escalonamento nesses casos: agende em vez de só transferir.\n"
        "\n"
        "ESCALONAMENTO (regra crítica):\n"
        "- NÃO escale por objeção, dúvida, comparação, hesitação ou frases como 'consórcio é ruim/furada', "
        "'redutor não presta', 'vou pensar', 'achei caro'. Isso é atendimento normal: reconheça a preocupação, "
        "explique com clareza, reposicione quando fizer sentido e só conduza para o próximo passo se isso couber "
        "na conversa. Objeção NUNCA é motivo de escalonamento.\n"
        "- Use `escalar_humano` SOMENTE quando o cliente: quiser fechar/contratar de fato; pedir boleto, contrato ou "
        "link de pagamento; perguntar taxa, administradora, grupo ou prazo de contemplação ESPECÍFICOS; falar de FGTS, "
        "quitação de financiamento, construção/reforma ou enviar documentos; estiver claramente insatisfeito/irritado; "
        "pedir explicitamente falar com um humano; ou trazer assunto totalmente fora de consórcio.\n"
        "- Na dúvida se deve escalar, NÃO escale: continue atendendo e conduzindo.\n"
        "- Se o cliente pedir para NÃO ser mais contatado ('não quero mais', 'me remova', 'pare de mandar mensagem'), "
        "chame `registrar_opt_out`, agradeça e encerre com educação. Não insista nem faça novas perguntas.\n"
        "- Ao escalar, escreva uma mensagem curta avisando que um especialista vai continuar.\n"
        "- MEMÓRIA: mais adiante há um bloco 'DADOS JÁ COLETADOS DESTE CLIENTE'. Trate-o como o que você já sabe. "
        "NÃO pergunte de novo o que já estiver lá. Se já tiver objetivo e valor de carta, PARE de perguntar/confirmar "
        "e AJA: rode `simular_consorcio` e/ou `gerar_proposta`, ou proponha a reunião. Sempre que descobrir um dado "
        "novo (prazo, parcela, reserva), chame `registrar_qualificacao` para salvar.\n\n"
        f"Administradoras disponíveis para esta organização: {admins}.\n"
        f"{cliente}\n\n"
        "===== BASE DE CONHECIMENTO (GLOBAL) =====\n" + knowledge
    )


def _is_context_noise(message: dict[str, Any]) -> bool:
    """Identifica mensagens operacionais que não devem treinar o próximo turno."""
    if (message.get("direction") or "").strip() != "out":
        return False

    payload = message.get("payload")
    if isinstance(payload, dict):
        if any(bool(payload.get(flag)) for flag in _CONTEXT_NOISE_PAYLOAD_FLAGS):
            return True
        # mensagens template de automação entram no histórico operacional, mas
        # atrapalham o contexto conversacional do agente.
        if message.get("msg_type") == "template" and not payload.get("manual_reply"):
            return True

    return False


def _history_to_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converte whatsapp_messages (in/out) em mensagens user/assistant limpas."""
    msgs: list[dict[str, Any]] = []
    for m in history:
        text = (m.get("body") or "").strip()
        if not text:
            continue
        if _is_context_noise(m):
            continue
        role = "user" if m.get("direction") == "in" else "assistant"
        # combina mensagens consecutivas do mesmo papel
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + text
        else:
            msgs.append({"role": role, "content": text})
    # a API exige começar com user...
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    # ...e terminar com user (claude-sonnet-5 não aceita prefill de assistant).
    while msgs and msgs[-1]["role"] != "user":
        msgs.pop()
    if len(msgs) > settings.WHATSAPP_AI_MAX_HISTORY:
        msgs = msgs[-settings.WHATSAPP_AI_MAX_HISTORY:]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        while msgs and msgs[-1]["role"] != "user":
            msgs.pop()
    return msgs


def lead_em_handoff(supa: Client, org_id: str, lead_id: Optional[str]) -> bool:
    """True se este lead já foi escalado para humano (IA fica em silêncio).

    Marcador: alguma mensagem de saída com payload.ai_handoff = true.
    """
    if not lead_id:
        return False
    try:
        resp = (
            supa.table("whatsapp_messages")
            .select("id")
            .eq("org_id", org_id)
            .eq("lead_id", lead_id)
            .filter("payload->>ai_handoff", "eq", "true")
            .limit(1)
            .execute()
        )
        return bool(getattr(resp, "data", None))
    except Exception:  # noqa: BLE001
        return False


def _exec_tool(
    *, name: str, args: dict[str, Any], supa: Client, org_id: str, lead_id: Optional[str], state: dict[str, Any]
) -> Any:
    if name == "simular_consorcio":
        return ai_tools.simular_consorcio(**args)
    if name == "registrar_opt_out":
        return ai_tools.registrar_opt_out(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
    if name == "buscar_dados_lead":
        return ai_tools.buscar_dados_lead(supa=supa, org_id=org_id, lead_id=lead_id or "")
    if name == "registrar_qualificacao":
        return ai_tools.registrar_qualificacao(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
    if name == "atualizar_etapa_classificacao":
        return ai_tools.atualizar_etapa_classificacao(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
    if name == "gerar_proposta":
        return ai_tools.gerar_proposta(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
    if name == "listar_horarios_disponiveis":
        return ai_tools.listar_horarios_disponiveis(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
    if name == "agendar_reuniao":
        return ai_tools.agendar_reuniao(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
    if name == "escalar_humano":
        state["escalated"] = True
        motivo = args.get("motivo") or "escalonamento"
        resumo = args.get("resumo") or ""
        try:
            supa.table("activities").insert(
                {
                    "id": str(uuid4()),
                    "org_id": org_id,
                    "lead_id": lead_id,
                    "tipo": "whatsapp",
                    "assunto": f"IA escalou para humano: {motivo}",
                    "conteudo": resumo,
                }
            ).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ia_handoff_log_falhou", extra={"org_id": org_id, "error": str(exc)})
        return {"ok": True, "instrucao": "Atendimento marcado para humano. Avise o cliente de forma breve e educada e encerre sua participação."}
    return {"erro": "ferramenta desconhecida"}


def run_agent(
    *,
    supa: Client,
    org_id: str,
    lead_id: Optional[str],
    history: list[dict[str, Any]],
    nome_cliente: Optional[str] = None,
) -> dict[str, Any]:
    """Roda o agente sobre o histórico da conversa. Retorna {reply, escalated}."""
    if not settings.ANTHROPIC_API_KEY.strip():
        return {"reply": None, "escalated": False, "erro": "ANTHROPIC_API_KEY ausente"}

    import anthropic  # import tardio (dependência opcional em dev)

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    administradoras = ai_tools.listar_administradoras(supa=supa, org_id=org_id)
    system_static = _build_system(org_administradoras=administradoras, nome_cliente=nome_cliente)
    dados_lead = _dados_coletados(supa, org_id, lead_id)
    # Bloco 1 (estático) fica cacheado; bloco 2 (dados do lead) varia por conversa.
    system_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": system_static, "cache_control": {"type": "ephemeral"}}
    ]
    if dados_lead:
        system_blocks.append(
            {"type": "text", "text": "===== DADOS JÁ COLETADOS DESTE CLIENTE =====\n" + dados_lead}
        )
    messages = _history_to_messages(history)
    if not messages:
        return {"reply": None, "escalated": False}

    state: dict[str, Any] = {"escalated": False}
    final_text: Optional[str] = None

    for _ in range(6):  # limite de iterações do loop de ferramentas
        try:
            resp = client.messages.create(
                model=settings.WHATSAPP_AI_MODEL,
                max_tokens=1024,
                system=system_blocks,
                thinking={"type": "disabled"},
                tools=_TOOLS,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 - erro de API (créditos/rate/modelo) não pode virar silêncio
            logger.exception("ia_model_call_falhou", extra={"org_id": org_id, "model": settings.WHATSAPP_AI_MODEL})
            return {"reply": None, "escalated": bool(state.get("escalated")), "erro": f"model_call: {exc}"}

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    try:
                        result = _exec_tool(
                            name=block.name, args=block.input or {}, supa=supa, org_id=org_id, lead_id=lead_id, state=state
                        )
                    except Exception as exc:  # noqa: BLE001 - falha de ferramenta não derruba o turno
                        logger.exception("ia_tool_falhou", extra={"org_id": org_id, "tool": block.name})
                        result = {"ok": False, "erro": f"falha na ferramenta {block.name}: {exc}"}
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        # resposta final (texto)
        final_text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        break

    # Loop esgotou sem texto (modelo só chamou ferramentas): força uma resposta
    # final SEM ferramentas para o cliente não ficar sem retorno.
    if not final_text:
        try:
            resp = client.messages.create(
                model=settings.WHATSAPP_AI_MODEL,
                max_tokens=1024,
                system=system_blocks,
                thinking={"type": "disabled"},
                messages=messages + [
                    {"role": "user", "content": "Responda ao cliente agora em texto, de forma natural, sem chamar ferramentas."}
                ],
            )
            final_text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        except Exception as exc:  # noqa: BLE001
            logger.exception("ia_forcar_texto_falhou", extra={"org_id": org_id})
            return {"reply": None, "escalated": bool(state.get("escalated")), "erro": f"forcar_texto: {exc}"}

    return {"reply": final_text or None, "escalated": bool(state.get("escalated"))}
