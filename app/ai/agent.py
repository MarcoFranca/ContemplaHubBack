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
import re
import unicodedata
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

_REUNIAO_KEYWORDS = (
    "reuniao",
    "agenda",
    "agendar",
    "horario",
    "horarios",
    "meet",
    "videochamada",
    "chamada",
    "link da reuniao",
    "link da reunião",
)
_REMARCAR_KEYWORDS = ("remarcar", "reagendar", "mudar horario", "mudar horário", "trocar horario", "trocar horário")
_CANCELAR_KEYWORDS = ("cancelar", "desmarcar", "desfazer")
_SIMULACAO_KEYWORDS = ("simul", "parcela", "valor da carta", "valor de carta", "quanto fica", "cenario", "cenário")
_PROPOSTA_KEYWORDS = ("proposta", "pdf", "orcamento", "orçamento", "cotacao", "cotação")
_SEGURO_KEYWORDS = ("seguro", "seguro de vida", "azos", "apolice", "apólice", "cobertura", "protecao", "proteção")
_HUMANO_KEYWORDS = ("humano", "pessoa", "atendente", "especialista", "consultor")
_OPTOUT_KEYWORDS = ("nao quero mais", "não quero mais", "pare de mandar", "me remova", "encerrar", "não me chame")


def _normalize_text(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    return unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"].strip()
    return ""


def _conversation_product(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        text = _normalize_text(str(message.get("content") or ""))
        if _contains_any(text, _SEGURO_KEYWORDS):
            return "seguro_azos"
        if _contains_any(text, ("consorcio", "carta de credito", "contemplacao", "lance")):
            return "consorcio"
    return None


def _infer_turn_intent(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return "desconhecida"
    if _contains_any(normalized, _OPTOUT_KEYWORDS):
        return "opt_out"
    if _contains_any(normalized, _SEGURO_KEYWORDS):
        return "seguro_azos"
    if _contains_any(normalized, _REMARCAR_KEYWORDS) and _contains_any(normalized, _REUNIAO_KEYWORDS):
        return "remarcacao_reuniao"
    if _contains_any(normalized, _CANCELAR_KEYWORDS) and _contains_any(normalized, _REUNIAO_KEYWORDS):
        return "cancelamento_reuniao"
    if _contains_any(normalized, _REUNIAO_KEYWORDS):
        return "reuniao"
    if _contains_any(normalized, _PROPOSTA_KEYWORDS):
        return "proposta"
    if _contains_any(normalized, _SIMULACAO_KEYWORDS):
        return "simulacao"
    if _contains_any(normalized, _HUMANO_KEYWORDS):
        return "humano"
    return "geral"


def _build_turn_guidance(last_user_message: str, intent: str) -> str:
    rules: list[str] = [
        "- A última mensagem do cliente tem prioridade máxima sobre assuntos antigos.",
        "- Responda primeiro ao pedido atual do cliente antes de retomar qualquer contexto anterior.",
        "- Não mude de assunto por conta própria.",
        "- Não invente que o cliente pediu simulação, proposta ou valores se isso não apareceu nesta mensagem.",
    ]

    if intent in {"reuniao", "remarcacao_reuniao", "cancelamento_reuniao"}:
        rules.extend(
            [
                "- Este turno é sobre reunião/agendamento. Não use `simular_consorcio` e não use `gerar_proposta`, a menos que o cliente peça isso explicitamente nesta mesma mensagem.",
                "- Se o cliente pedir remarcação, trate como remarcação de agenda: reconheça o pedido, consulte horários disponíveis e conduza só dentro desse assunto.",
                "- Se já existir reunião ativa na memória, use isso para responder com coerência.",
            ]
        )
    elif intent == "proposta":
        rules.extend(
            [
                "- Este turno é sobre proposta. Não desvie para reunião ou simulação diferente sem responder o pedido de proposta primeiro.",
            ]
        )
    elif intent == "simulacao":
        rules.extend(
            [
                "- Este turno é sobre números/simulação. Só simule se houver dados mínimos ou se fizer sentido pedir os dados que faltam.",
            ]
        )
    elif intent == "seguro_azos":
        rules.extend(
            [
                "Este turno é sobre Seguro de Vida Azos. Não use ferramentas, proposta ou números de Consórcio.",
                "Para cotar, colete somente os dados necessários e confirme consentimento explícito antes de consultar a Azos.",
            ]
        )
    elif intent == "opt_out":
        rules.extend(
            [
                "- Este turno é de encerramento/opt-out. Não conduza venda.",
            ]
        )

    return (
        "===== PRIORIDADE DESTE TURNO =====\n"
        f"Última mensagem literal do cliente: {last_user_message or '(vazia)'}\n"
        f"Intenção aparente do turno: {intent}\n"
        "Regras obrigatórias deste turno:\n"
        + "\n".join(rules)
    )


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
        "name": "buscar_profissoes_azos",
        "description": "Busca a profissão informada no catálogo Azos. Pode devolver correspondência exata ou até 3 alternativas próximas para o cliente confirmar. Nunca escolha uma alternativa pelo cliente e nunca repita a mesma busca em loop.",
        "input_schema": {"type": "object", "properties": {"termo": {"type": "string"}}, "required": ["termo"]},
    },
    {
        "name": "consultar_coberturas_azos",
        "description": "Consulta coberturas elegíveis de Seguro de Vida Azos. Só use após consentimento explícito do cliente para uso dos dados na cotação.",
        "input_schema": {"type": "object", "properties": {"perfil": {"type": "object", "description": "data_nascimento (YYYY-MM-DD), sexo m/f, altura_m, peso_kg, fumante, renda_mensal, profissao_id e consentimento_confirmado=true"}}, "required": ["perfil"]},
    },
    {
        "name": "gerar_cotacao_vida_azos",
        "description": "Calcula e publica cotação Azos com prêmio mensal, justificativas e link público próprio de Seguro. Use apenas com coberturas e capitais escolhidos entre as opções retornadas pela recomendação e confirmados pelo cliente.",
        "input_schema": {"type": "object", "properties": {"perfil": {"type": "object"}, "coberturas": {"type": "array", "items": {"type": "object", "properties": {"code": {"type": "string"}, "capital": {"type": "number"}}, "required": ["code", "capital"]}}, "recomendacao": {"type": "object"}, "diagnostico": {"type": "object"}}, "required": ["perfil", "coberturas"]},
    },
    {
        "name": "montar_recomendacao_vida_azos",
        "description": "Monta uma sugestão explicável de coberturas e capitais considerando renda, autonomia profissional, filhos/dependentes, dívidas, reserva e orçamento. Use depois de consultar coberturas e antes de gerar a cotação.",
        "input_schema": {"type": "object", "properties": {"coberturas": {"type": "array", "items": {"type": "object"}}, "diagnostico": {"type": "object", "description": "renda_mensal, profissao, autonomo, dependentes, filhos, dividas_saldo, reserva_meses e orcamento_mensal"}}, "required": ["coberturas", "diagnostico"]},
    },
    {
        "name": "encaminhar_corretor_azos",
        "description": "Encaminha ao corretor somente quando o cliente confirmar que quer seguir/finalizar o Seguro Azos. Cria atendimento pendente e encerra a participação da IA.",
        "input_schema": {"type": "object", "properties": {"cotacao_id": {"type": "string"}, "resumo": {"type": "string"}}, "required": []},
    },
    {
        "name": "simular_consorcio",
        "description": (
            "Estimativa de consórcio com FOCO NO REDUTOR (parcela reduzida até a contemplação). Usa a campanha ativa "
            "da org (ou o padrão). Informe 'valor_credito' quando o cliente quer uma carta específica; OU informe "
            "'parcela_alvo' (valor mensal confortável) quando ele diz quanto pode pagar, e a ferramenta calcula o "
            "MAIOR crédito que cabe nessa parcela com redutor (maior carta pelo menor valor). É sempre estimativa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "produto": {"type": "string", "enum": ["imovel", "auto", "pesados"]},
                "valor_credito": {"type": "number", "description": "Valor da carta pretendido (use quando o cliente quer uma carta específica)"},
                "parcela_alvo": {"type": "number", "description": "Parcela mensal confortável do cliente (use para achar o maior crédito com redutor)"},
                "prazo": {"type": "integer", "description": "Prazo em meses (opcional; senão usa o da campanha/produto)"},
                "redutor_percentual": {"type": "number", "description": "Redutor específico, se o cliente pedir (opcional; senão usa o da campanha)"},
                "lance_percentual": {"type": "number", "description": "Percentual de lance para estimar o valor (opcional)"},
            },
            "required": ["produto"],
        },
    },
    {
        "name": "listar_campanhas",
        "description": "Consulta as campanhas ativas da org (taxa, redutor, fundo de reserva por administradora/produto). Use antes de estimar para usar as condições vigentes. Se não houver, o sistema usa o padrão.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
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
            "Agenda OU REMARCA a reunião do cliente. Use quando ele aceitar um horário específico. SEMPRE confirme o "
            "dia e a hora ANTES de chamar. Passe 'inicio' em ISO 8601 com fuso -03:00. Se já existir uma reunião ativa "
            "do lead, esta ferramenta ATUALIZA aquela reunião (não cria outra), então use-a também para remarcar. Se "
            "o horário voltar indisponível, ofereça outro."
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
        "name": "cancelar_reuniao",
        "description": (
            "Cancela a reunião ativa do lead. Use SOMENTE depois de tentar remarcar e o cliente não quiser um novo "
            "horário agora. Se ele concordar em retomar depois, passe 'retornar_em' (ISO 8601) para o sistema tentar "
            "reagendar nessa data. PREFIRA remarcar (agendar_reuniao) a cancelar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "description": "Por que cancelou (curto)"},
                "retornar_em": {"type": "string", "description": "Quando tentar reagendar, ISO 8601 (opcional)"},
            },
            "required": [],
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
        lr = (
            supa.table("leads")
            .select("nome, etapa, temperatura, valor_interesse, prazo_meses")
            .eq("org_id", org_id)
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        lead = (getattr(lr, "data", None) or [{}])[0]
        ir = (
            supa.table("lead_interesses")
            .select("produto, objetivo, perfil_desejado, observacao, created_at")
            .eq("org_id", org_id).eq("lead_id", lead_id).order("created_at", desc=True).limit(1).execute()
        )
        interesse = (getattr(ir, "data", None) or [{}])[0]
        proposta_resp = (
            supa.table("lead_propostas")
            .select("id, status, titulo, created_at")
            .eq("org_id", org_id)
            .eq("lead_id", lead_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        proposta = (getattr(proposta_resp, "data", None) or [{}])[0]
        ag_resp = (
            supa.table("agendamentos")
            .select("id, titulo, inicio, status")
            .eq("org_id", org_id)
            .eq("lead_id", lead_id)
            .order("inicio", desc=False)
            .limit(5)
            .execute()
        )
        agendamentos = getattr(ag_resp, "data", None) or []
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
    if proposta.get("id"):
        titulo = proposta.get("titulo") or "proposta sem título"
        status = proposta.get("status") or "sem status"
        linhas.append(f"- Última proposta gerada: {titulo} (status: {status})")
    ag_ativos = [a for a in agendamentos if (a.get("status") or "") in {"agendado", "confirmado"}]
    if ag_ativos:
        prox = ag_ativos[0]
        when = prox.get("inicio")
        titulo = prox.get("titulo") or "Reunião com especialista"
        linhas.append(f"- Já existe reunião ativa agendada: {titulo} em {when}")
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
        "- SEGURO DE VIDA AZOS é um fluxo separado de Consórcio. Quando o cliente falar de seguro, use somente `buscar_profissoes_azos`, `consultar_coberturas_azos`, `montar_recomendacao_vida_azos`, `gerar_cotacao_vida_azos` e `encaminhar_corretor_azos`. Nunca use `simular_consorcio` ou `gerar_proposta` para Seguro.\n"
        "- COLETA GUIADA DE SEGURO: use no máximo 3 blocos curtos, sem perguntar campo por campo. Bloco 1: nascimento, sexo, altura e peso na mesma mensagem. Bloco 2: profissão, vínculo (autônomo/CLT/outro), renda e tabagismo. Bloco 3: filhos/dependentes, dívidas, reserva e faixa mensal confortável. Diga que o cliente pode responder cada bloco em uma única mensagem.\n"
        "- Para escolhas padrão com até 3 respostas, termine a mensagem com o marcador `[[BOTOES:Opção 1|Opção 2|Opção 3]]`; o backend transformará em botões do WhatsApp. Use especialmente para Sim/Não, Autônomo/CLT/Outro e consentimento. Não mostre o marcador ao cliente fora desse formato.\n"
        "- MEMÓRIA DO SEGURO: releia todo o histórico disponível antes de perguntar. Nunca diga que não encontrou um dado que aparece na conversa. Se o cliente corrigir um dado, use a versão mais recente. Antes do consentimento, faça um resumo único do que já foi coletado e peça apenas o que realmente estiver faltando.\n"
        "- PROFISSÃO AZOS: chame `buscar_profissoes_azos` apenas uma vez para cada termo informado. Se retornar `tipo_correspondencia=alternativa`, explique que o catálogo não possui o nome exato e mostre as opções em botões para o cliente escolher. Nunca selecione ocupação diferente sem confirmação. Se retornar `nao_encontrada`, peça uma única descrição da atividade principal e, persistindo, encaminhe ao corretor; não entre em loop.\n"
        "- Antes de enviar o perfil à Azos, obtenha consentimento explícito do cliente. Não persista dados parciais: consulte a Azos só com todos os campos e consentimento.\n"
        "- Depois de `consultar_coberturas_azos`, chame obrigatoriamente `montar_recomendacao_vida_azos` e reproduza a cobertura devolvida pela ferramenta. Se DG30 estiver elegível, ela é a opção padrão; só use DG13 se DG30 não estiver disponível ou se o cliente escolher conscientemente a alternativa mais enxuta após ver a diferença.\n"
        "- Explique a lógica em linguagem simples e peça confirmação ou ajustes; não recomende automaticamente o menor capital e não empurre coberturas. Respeite o orçamento, mas sinalize quando reduzir capital deixa uma necessidade relevante descoberta.\n"
        "- Após `gerar_cotacao_vida_azos`, separe visualmente: `CAPITAL SEGURADO` é o valor de proteção/indenização potencial de cada cobertura; `PRÊMIO MENSAL` é o valor total pago por mês. Nunca apresente prêmio como se fosse capital. Envie o link e diga que a cotação está sujeita à análise da Azos e pode ser ajustada.\n"
        "- SIMULAÇÃO com foco no REDUTOR: ao falar de parcela, NÃO use parcela cheia. Trabalhe com a parcela "
        "REDUZIDA (redutor até a contemplação), que dá o maior crédito pelo menor valor. Quando o cliente disser "
        "quanto pode pagar por mês, chame `simular_consorcio` com `parcela_alvo` (não invente a carta): a ferramenta "
        "acha o maior crédito que cabe naquela parcela com redutor. Ex.: 'quer 350 mil pagando 1200' -> mostre a "
        "carta possível com a parcela reduzida. Use `listar_campanhas` para as condições vigentes (senão o sistema "
        "usa o padrão). Deixe SEMPRE claro que é uma estimativa e que o valor final é definido na reunião com o "
        "corretor. Nunca apresente como valor fechado.\n"
        "- NÃO cite operadora/administradora específica (ex.: Porto) ao apresentar condições ou estimativas. "
        "Trabalhamos com várias operadoras; revelar isso pode engessar a negociação. Fale de forma genérica ('nas "
        "condições que trabalhamos', 'as campanhas atuais') e deixe a operadora para a reunião com o corretor.\n"
        "- Use `registrar_qualificacao` conforme for descobrindo dados.\n"
        "- Use `atualizar_etapa_classificacao` para mover o lead no funil e classificar a temperatura sempre que a "
        "conversa avançar (respondeu, começou a qualificar, recebeu proposta, negociando, esfriou). Isso mantém o "
        "kanban do time atualizado sozinho. Não anuncie isso ao cliente, faça em segundo plano.\n"
        "- Use `gerar_proposta` quando o cliente demonstrar intenção real e você já tiver produto e valor de carta, "
        "especialmente se ele pedir números, proposta, comparação ou um cenário mais concreto. A ferramenta cria e "
        "envia a proposta e devolve um link; mande esse link ao cliente de forma natural. Pedir proposta NÃO é "
        "escalonamento: você mesmo gera. DEPOIS de enviar a proposta, NÃO pare de forma passiva: convide o cliente "
        "para uma reunião com o corretor para explicar a proposta, e faça isso em uma mensagem SEPARADA (use '|||' "
        "para separar: primeiro a mensagem da proposta com o link, depois a mensagem do convite para reunião).\n"
        "- MENSAGENS SEPARADAS: quando fizer sentido enviar mais de uma mensagem seguida (ex.: proposta e depois o "
        "convite para reunião, ou uma explicação curta e depois uma pergunta), separe-as com '|||'. Use no máximo 2 "
        "ou 3 mensagens e só quando ajudar de verdade; não fragmente à toa.\n"
        "- Para agendar reunião com especialista: primeiro chame `listar_horarios_disponiveis` e ofereça ao cliente "
        "APENAS os horários retornados (nunca invente). Quando ele escolher, chame `agendar_reuniao` com o 'inicio' "
        f"daquele horário. A data/hora atual é {_agora_brasil()}. Confirme ao cliente o horário marcado. Isso "
        "substitui o escalonamento nesses casos: agende em vez de só transferir.\n"
        "- CANCELAMENTO de reunião: se o cliente quiser cancelar, PRIMEIRO tente REMARCAR (ofereça novos horários com "
        "`listar_horarios_disponiveis` e use `agendar_reuniao`, que atualiza a reunião existente, sem duplicar). Só "
        "chame `cancelar_reuniao` se ele não quiser um novo horário agora; nesse caso, combine uma data para retomar "
        "e passe 'retornar_em' para o sistema tentar reagendar depois. Ao remarcar, sempre use agendar_reuniao (nunca "
        "crie uma segunda reunião).\n"
        "- Se o bloco de memória indicar que já existe proposta enviada, não gere outra sem motivo claro. Primeiro "
        "retome a proposta existente, responda dúvidas, compare cenários ou avance para reunião.\n"
        "- Se o bloco de memória indicar que já existe reunião agendada, não ofereça novo agendamento por padrão. "
        "Priorize confirmação, preparação e esclarecimentos objetivos.\n"
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
    history_limit = settings.WHATSAPP_AI_MAX_HISTORY
    if _conversation_product(msgs) == "seguro_azos":
        # A jornada de Seguro coleta vários campos em turnos sucessivos. Preserva
        # o ciclo inteiro para não pedir novamente dados que saíram da janela curta.
        history_limit = max(history_limit, 40)
    if len(msgs) > history_limit:
        msgs = msgs[-history_limit:]
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
    if name == "buscar_profissoes_azos":
        return ai_tools.buscar_profissoes_azos(**args)
    if name == "consultar_coberturas_azos":
        return ai_tools.consultar_coberturas_azos(**args)
    if name == "montar_recomendacao_vida_azos":
        return ai_tools.montar_recomendacao_vida_azos(**args)
    if name == "gerar_cotacao_vida_azos":
        return ai_tools.gerar_cotacao_vida_azos(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
    if name == "encaminhar_corretor_azos":
        result = ai_tools.encaminhar_corretor_azos(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
        if result.get("ok"):
            state["escalated"] = True
            state["handoff_reason"] = "Seguro de Vida Azos: cliente pediu continuidade com corretor"
        return result
    if name == "simular_consorcio":
        return ai_tools.simular_consorcio(supa=supa, org_id=org_id, **args)
    if name == "listar_campanhas":
        return ai_tools.listar_campanhas(supa=supa, org_id=org_id)
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
    if name == "cancelar_reuniao":
        return ai_tools.cancelar_reuniao(supa=supa, org_id=org_id, lead_id=lead_id or "", **args)
    if name == "escalar_humano":
        state["escalated"] = True
        motivo = args.get("motivo") or "escalonamento"
        resumo = args.get("resumo") or ""
        state["handoff_reason"] = motivo
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
        return {"reply": None, "escalated": False, "handoff_reason": None, "erro": "ANTHROPIC_API_KEY ausente"}

    import anthropic  # import tardio (dependência opcional em dev)

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    administradoras = ai_tools.listar_administradoras(supa=supa, org_id=org_id)
    system_static = _build_system(org_administradoras=administradoras, nome_cliente=nome_cliente)
    dados_lead = _dados_coletados(supa, org_id, lead_id)
    messages = _history_to_messages(history)
    if not messages:
        return {"reply": None, "escalated": False, "handoff_reason": None}

    last_user_message = _last_user_text(messages)
    turn_intent = _infer_turn_intent(last_user_message)
    product_context = "seguro_azos" if turn_intent == "seguro_azos" else _conversation_product(messages)
    logger.info(
        "whatsapp_ai_turn_intent",
        extra={"org_id": org_id, "lead_id": lead_id, "intent": turn_intent, "last_user_message": last_user_message[:200]},
    )

    # Bloco 1 (estático) fica cacheado; bloco 2 (dados do lead) varia por conversa.
    system_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": system_static, "cache_control": {"type": "ephemeral"}}
    ]
    if dados_lead:
        system_blocks.append(
            {"type": "text", "text": "===== DADOS JÁ COLETADOS DESTE CLIENTE =====\n" + dados_lead}
        )
    system_blocks.append(
        {"type": "text", "text": _build_turn_guidance(last_user_message, turn_intent)}
    )

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
            return {
                "reply": None,
                "escalated": bool(state.get("escalated")),
                "handoff_reason": state.get("handoff_reason"),
                "erro": f"model_call: {exc}",
            }

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
            return {
                "reply": None,
                "escalated": bool(state.get("escalated")),
                "handoff_reason": state.get("handoff_reason"),
                "erro": f"forcar_texto: {exc}",
            }

    return {
        "reply": final_text or None,
        "escalated": bool(state.get("escalated")),
        "handoff_reason": state.get("handoff_reason"),
        "product_context": product_context,
    }
