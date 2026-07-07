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
        "name": "buscar_dados_lead",
        "description": "Consulta os dados já salvos do lead (nome, telefone, interesse) para não repetir perguntas.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "escalar_humano",
        "description": "Transfere para um especialista humano. Use SOMENTE nos gatilhos reais: pedido de proposta/fechamento/contrato/boleto; taxa, administradora, grupo ou prazo de contemplação específicos; FGTS, quitação de financiamento, construção/reforma, documentos; cliente insatisfeito/irritado; pedido explícito de humano; assunto fora de consórcio. NÃO use para objeções, dúvidas, comparações ou hesitação ('consórcio é ruim', 'vou pensar', 'achei caro') - isso você mesmo responde e conduz.",
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


def _build_system(*, org_administradoras: list[str], nome_cliente: Optional[str]) -> str:
    knowledge = _load_knowledge()
    admins = ", ".join(org_administradoras) if org_administradoras else "(nenhuma cadastrada; se perguntarem administradora específica, escale para humano)"
    cliente = f"O cliente se chama {nome_cliente}." if nome_cliente else "Você ainda não sabe o nome do cliente."
    return (
        "Você é o assistente virtual de atendimento de uma consultoria de consórcio, atendendo pelo WhatsApp.\n"
        "Siga ESTRITAMENTE a base de conhecimento abaixo (identidade, tom, objeções, FAQ, qualificação, "
        "processo de venda e compliance). As regras de compliance e o tom prevalecem sobre tudo.\n\n"
        "Regras operacionais adicionais:\n"
        "- Responda em pt-BR, mensagens curtas e naturais para WhatsApp. Sem travessão (—).\n"
        "- NUNCA invente taxas, administradoras, grupos, prazos ou percentuais. Use as ferramentas e os dados da org.\n"
        "- Use `simular_consorcio` para números; use `registrar_qualificacao` conforme for descobrindo dados.\n"
        "\n"
        "ESCALONAMENTO (regra crítica):\n"
        "- NÃO escale por objeção, dúvida, comparação, hesitação ou frases como 'consórcio é ruim/furada', "
        "'redutor não presta', 'vou pensar', 'achei caro'. Isso é atendimento normal: RECONHEÇA, EXPLIQUE, "
        "REPOSICIONE e CONDUZA com uma pergunta (siga o arquivo de objeções). Objeção NUNCA é motivo de escalonamento.\n"
        "- Use `escalar_humano` SOMENTE quando o cliente: pedir proposta/fechar/contratar; pedir boleto, contrato ou "
        "link de pagamento; perguntar taxa, administradora, grupo ou prazo de contemplação ESPECÍFICOS; falar de FGTS, "
        "quitação de financiamento, construção/reforma ou enviar documentos; estiver claramente insatisfeito/irritado; "
        "pedir explicitamente falar com um humano; ou trazer assunto totalmente fora de consórcio.\n"
        "- Na dúvida se deve escalar, NÃO escale: continue atendendo e conduzindo.\n"
        "- Ao escalar, escreva uma mensagem curta avisando que um especialista vai continuar.\n\n"
        f"Administradoras disponíveis para esta organização: {admins}.\n"
        f"{cliente}\n\n"
        "===== BASE DE CONHECIMENTO (GLOBAL) =====\n" + knowledge
    )


def _history_to_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converte whatsapp_messages (in/out) em mensagens user/assistant."""
    msgs: list[dict[str, Any]] = []
    for m in history:
        text = (m.get("body") or "").strip()
        if not text:
            continue
        role = "user" if m.get("direction") == "in" else "assistant"
        # combina mensagens consecutivas do mesmo papel
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + text
        else:
            msgs.append({"role": role, "content": text})
    # a API exige começar com user
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
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
    if name == "buscar_dados_lead":
        return ai_tools.buscar_dados_lead(supa=supa, org_id=org_id, lead_id=lead_id or "")
    if name == "registrar_qualificacao":
        return ai_tools.registrar_qualificacao(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
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
    system = _build_system(org_administradoras=administradoras, nome_cliente=nome_cliente)
    messages = _history_to_messages(history[-settings.WHATSAPP_AI_MAX_HISTORY:])
    if not messages:
        return {"reply": None, "escalated": False}

    state: dict[str, Any] = {"escalated": False}
    final_text: Optional[str] = None

    for _ in range(6):  # limite de iterações do loop de ferramentas
        resp = client.messages.create(
            model=settings.WHATSAPP_AI_MODEL,
            max_tokens=1024,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "disabled"},
            tools=_TOOLS,
            messages=messages,
        )
        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    result = _exec_tool(
                        name=block.name, args=block.input or {}, supa=supa, org_id=org_id, lead_id=lead_id, state=state
                    )
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        # resposta final (texto)
        final_text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        break

    return {"reply": final_text or None, "escalated": bool(state.get("escalated"))}
