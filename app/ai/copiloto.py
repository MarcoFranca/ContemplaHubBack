"""Copiloto interno do corretor: Claude + ferramentas sobre o CRM.

Diferente do agente do WhatsApp (que fala com o cliente), este assiste o
USUÁRIO logado dentro do sistema. Todas as ferramentas são escopadas por
`org_id`. Ações de escrita só depois de confirmação do usuário na conversa.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from supabase import Client

from app.core.config import settings

logger = logging.getLogger(__name__)

_ORIGEM_PADRAO = "outro"  # enum canal_origem (não existe "manual")


# --------------------------------------------------------------------------- #
# Ferramentas (todas escopadas por org_id)
# --------------------------------------------------------------------------- #
def _buscar_clientes(supa: Client, org_id: str, termo: str) -> dict[str, Any]:
    termo = (termo or "").strip()
    if not termo:
        return {"erro": "informe um nome para buscar"}
    try:
        resp = (
            supa.table("leads")
            .select("id, nome, telefone, email, etapa")
            .eq("org_id", org_id)
            .ilike("nome", f"%{termo}%")
            .limit(10)
            .execute()
        )
        leads = getattr(resp, "data", None) or []
        ids = [l["id"] for l in leads]
        counts: dict[str, int] = {}
        if ids:
            cot = supa.table("cotas").select("lead_id").eq("org_id", org_id).in_("lead_id", ids).execute()
            for c in getattr(cot, "data", None) or []:
                lid = c.get("lead_id")
                if lid:
                    counts[lid] = counts.get(lid, 0) + 1
        return {
            "clientes": [
                {"lead_id": l["id"], "nome": l.get("nome"), "telefone": l.get("telefone"), "cartas": counts.get(l["id"], 0)}
                for l in leads
            ]
        }
    except Exception as exc:  # noqa: BLE001
        return {"erro": str(exc)}


def _resumo_cliente(supa: Client, org_id: str, lead_id: str) -> dict[str, Any]:
    try:
        lr = supa.table("leads").select("nome, telefone, email, etapa").eq("org_id", org_id).eq("id", lead_id).limit(1).execute()
        rows = getattr(lr, "data", None) or []
        if not rows:
            return {"erro": "cliente não encontrado"}
        lead = rows[0]
        cotas = getattr(
            supa.table("cotas")
            .select("numero_cota, grupo_codigo, valor_carta, status, tipo_lance_preferencial")
            .eq("org_id", org_id)
            .eq("lead_id", lead_id)
            .execute(),
            "data",
            None,
        ) or []
        alertas = getattr(
            supa.table("cota_alertas").select("id").eq("org_id", org_id).eq("lead_id", lead_id).eq("status", "pendente").execute(),
            "data",
            None,
        ) or []
        return {
            "nome": lead.get("nome"),
            "telefone": lead.get("telefone"),
            "email": lead.get("email"),
            "etapa": lead.get("etapa"),
            "total_cartas": len(cotas),
            "cartas": cotas,
            "alertas_pendentes": len(alertas),
        }
    except Exception as exc:  # noqa: BLE001
        return {"erro": str(exc)}


def _contar_cartas(supa: Client, org_id: str, lead_id: str) -> dict[str, Any]:
    try:
        cot = supa.table("cotas").select("id").eq("org_id", org_id).eq("lead_id", lead_id).execute()
        return {"total_cartas": len(getattr(cot, "data", None) or [])}
    except Exception as exc:  # noqa: BLE001
        return {"erro": str(exc)}


def _valor_cartas(supa: Client, org_id: str, lead_id: str) -> dict[str, Any]:
    """Valor total das cartas do cliente e o total das contempladas."""
    try:
        cotas = getattr(
            supa.table("cotas").select("id, valor_carta").eq("org_id", org_id).eq("lead_id", lead_id).execute(),
            "data",
            None,
        ) or []
        ids = [c["id"] for c in cotas]
        contempladas: set[str] = set()
        if ids:
            contr = getattr(
                supa.table("contratos").select("cota_id, status, data_contemplacao").eq("org_id", org_id).in_("cota_id", ids).execute(),
                "data",
                None,
            ) or []
            for c in contr:
                if (c.get("status") == "contemplado") or c.get("data_contemplacao"):
                    if c.get("cota_id"):
                        contempladas.add(c["cota_id"])

        def val(c: dict[str, Any]) -> float:
            return float(c.get("valor_carta") or 0)

        total = sum(val(c) for c in cotas)
        total_contempladas = sum(val(c) for c in cotas if c["id"] in contempladas)
        return {
            "total_cartas": len(cotas),
            "valor_total": round(total, 2),
            "contempladas_qtd": len(contempladas),
            "valor_contempladas": round(total_contempladas, 2),
        }
    except Exception as exc:  # noqa: BLE001
        return {"erro": str(exc)}


def _listar_alertas(supa: Client, org_id: str, apenas_vencidos: bool = False) -> dict[str, Any]:
    from datetime import date

    try:
        q = supa.table("cota_alertas").select("data, mensagem, lead_id").eq("org_id", org_id).eq("status", "pendente")
        if apenas_vencidos:
            q = q.lte("data", date.today().isoformat())
        rows = getattr(q.order("data", desc=False).limit(50).execute(), "data", None) or []
        return {"alertas": rows, "total": len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {"erro": str(exc)}


def _criar_cliente(
    supa: Client, org_id: str, user_id: Optional[str], nome: str, telefone: Optional[str] = None,
    email: Optional[str] = None, origem: Optional[str] = None,
) -> dict[str, Any]:
    nome = (nome or "").strip()
    if not nome:
        return {"ok": False, "erro": "nome é obrigatório"}
    try:
        payload = {
            "org_id": org_id,
            "nome": nome,
            "telefone": (telefone or "").strip() or None,
            "email": (email or "").strip() or None,
            "etapa": "novo",
            "origem": origem or _ORIGEM_PADRAO,
            "channel": "manual",
            "created_by": user_id,
        }
        ins = supa.table("leads").insert(payload).execute()
        rows = getattr(ins, "data", None) or []
        return {"ok": True, "lead_id": rows[0].get("id") if rows else None, "nome": nome}
    except Exception as exc:  # noqa: BLE001
        logger.warning("copiloto_criar_cliente_falhou", extra={"org_id": org_id, "error": str(exc)})
        return {"ok": False, "erro": str(exc)}


_TOOLS = [
    {
        "name": "buscar_clientes",
        "description": "Busca clientes (leads) por nome. Retorna os que baterem, com telefone e quantas cartas cada um tem. Use para achar o cliente antes de responder ou detalhar.",
        "input_schema": {"type": "object", "properties": {"termo": {"type": "string"}}, "required": ["termo"]},
    },
    {
        "name": "resumo_cliente",
        "description": "Resumo de um cliente pelo lead_id: dados, etapa, cartas (com valor/situação/lance) e alertas pendentes.",
        "input_schema": {"type": "object", "properties": {"lead_id": {"type": "string"}}, "required": ["lead_id"]},
    },
    {
        "name": "contar_cartas",
        "description": "Conta quantas cartas (cotas) um cliente tem, pelo lead_id.",
        "input_schema": {"type": "object", "properties": {"lead_id": {"type": "string"}}, "required": ["lead_id"]},
    },
    {
        "name": "valor_cartas",
        "description": "Valor total das cartas de um cliente (pelo lead_id) e o total das cartas contempladas (com quantidade). Use para 'valor total das cartas', 'quanto em contempladas', etc.",
        "input_schema": {"type": "object", "properties": {"lead_id": {"type": "string"}}, "required": ["lead_id"]},
    },
    {
        "name": "listar_alertas",
        "description": "Lista alertas de carta pendentes da organização. Use apenas_vencidos=true para os que vencem hoje ou já venceram.",
        "input_schema": {"type": "object", "properties": {"apenas_vencidos": {"type": "boolean"}}, "required": []},
    },
    {
        "name": "criar_cliente",
        "description": "Cadastra um novo cliente (lead). AÇÃO DE ESCRITA: só chame DEPOIS de confirmar os dados com o usuário. Nome é obrigatório; telefone e e-mail se houver.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nome": {"type": "string"},
                "telefone": {"type": "string"},
                "email": {"type": "string"},
            },
            "required": ["nome"],
        },
    },
]


def _exec_tool(*, name: str, args: dict[str, Any], supa: Client, org_id: str, user_id: Optional[str]) -> Any:
    if name == "buscar_clientes":
        return _buscar_clientes(supa, org_id, args.get("termo", ""))
    if name == "resumo_cliente":
        return _resumo_cliente(supa, org_id, args.get("lead_id", ""))
    if name == "contar_cartas":
        return _contar_cartas(supa, org_id, args.get("lead_id", ""))
    if name == "valor_cartas":
        return _valor_cartas(supa, org_id, args.get("lead_id", ""))
    if name == "listar_alertas":
        return _listar_alertas(supa, org_id, bool(args.get("apenas_vencidos")))
    if name == "criar_cliente":
        return _criar_cliente(supa, org_id, user_id, args.get("nome", ""), args.get("telefone"), args.get("email"))
    return {"erro": "ferramenta desconhecida"}


_SYSTEM = (
    "Você é o copiloto interno do ContemplaHub, um assistente que ajuda o CORRETOR/gestor a operar o sistema. "
    "Responde em pt-BR, de forma curta e direta. Sem travessão (—).\n"
    "- Você só enxerga e altera dados da organização do usuário (já garantido pelas ferramentas). Nunca invente números: "
    "use as ferramentas e responda com o que elas retornarem.\n"
    "- Para perguntas sobre um cliente ('quantas cartas o Lucas tem', 'resumo do fulano'), primeiro use `buscar_clientes`. "
    "Se houver mais de um com o nome, LISTE os encontrados e pergunte qual antes de detalhar.\n"
    "- AÇÕES DE ESCRITA (ex.: `criar_cliente`): NUNCA execute direto. Primeiro colete os dados que faltam, mostre um "
    "resumo do que vai fazer e peça a confirmação do usuário. Só chame a ferramenta depois de um 'sim'/'confirmo'.\n"
    "- Se faltar informação para uma ação, pergunte apenas o essencial.\n"
    "- Seja um copiloto de trabalho: objetivo, prestativo e seguro."
)


def run_copiloto(*, supa: Client, org_id: str, user_id: Optional[str], messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Roda o loop de tool-use sobre a conversa. `messages` = [{role, content}]."""
    if not settings.ANTHROPIC_API_KEY.strip():
        return {"reply": None, "erro": "ANTHROPIC_API_KEY ausente"}

    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    convo: list[dict[str, Any]] = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip()
    ]
    if not convo or convo[-1]["role"] != "user":
        return {"reply": None, "erro": "conversa inválida"}

    final_text: Optional[str] = None
    for _ in range(6):
        try:
            resp = client.messages.create(
                model=settings.COPILOTO_MODEL,
                max_tokens=1024,
                system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
                thinking={"type": "disabled"},
                tools=_TOOLS,
                messages=convo,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("copiloto_model_falhou", extra={"org_id": org_id})
            return {"reply": None, "erro": f"model_call: {exc}"}

        if resp.stop_reason == "tool_use":
            convo.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    try:
                        result = _exec_tool(name=block.name, args=block.input or {}, supa=supa, org_id=org_id, user_id=user_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("copiloto_tool_falhou", extra={"org_id": org_id, "tool": block.name})
                        result = {"erro": str(exc)}
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(result)})
            convo.append({"role": "user", "content": results})
            continue

        final_text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        break

    return {"reply": final_text or "Não consegui responder agora."}
