from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from supabase import Client

from app.core.config import settings
from app.deps import get_supabase_admin
from app.security.auth import AuthContext
from app.security.permissions import require_internal_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["copiloto"])


class CopilotoMessage(BaseModel):
    role: str
    content: str


class CopilotoChatIn(BaseModel):
    messages: list[CopilotoMessage] = Field(default_factory=list)


class CopilotoChatOut(BaseModel):
    reply: str


@router.post("/copiloto/chat", response_model=CopilotoChatOut)
def copiloto_chat(
    payload: CopilotoChatIn,
    supa: Client = Depends(get_supabase_admin),
    ctx: AuthContext = Depends(require_internal_user),
):
    if not settings.COPILOTO_ENABLED:
        raise HTTPException(status_code=503, detail="Copiloto desativado.")

    from app.ai import copiloto as copiloto_engine

    msgs = [{"role": m.role, "content": m.content} for m in payload.messages]
    try:
        result = copiloto_engine.run_copiloto(supa=supa, org_id=ctx.org_id, user_id=ctx.user_id, messages=msgs)
    except Exception as exc:  # noqa: BLE001
        logger.exception("copiloto_chat_falhou", extra={"org_id": ctx.org_id})
        raise HTTPException(status_code=502, detail=f"Falha no copiloto: {exc}")

    reply = result.get("reply")
    if not reply:
        raise HTTPException(status_code=502, detail=result.get("erro") or "Sem resposta.")
    return {"reply": reply}
