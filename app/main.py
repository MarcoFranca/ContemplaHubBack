# app/main.py
import asyncio
import logging

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware

from app.deps import get_supabase_admin

from app.routers import marketing_guide, marketing_guide_pdf
from app.routers.lead_propostas import router as lead_propostas_router
from app.routers.lead_cadastros import router as lead_cadastros_router
from app.routers.health import router as health_router
from app.routers.leads import router as leads_router
from app.routers.kanban import router as kanban_router
from app.routers.diagnostic import router as diagnostic_router
from app.routers.contracts import router as contracts_router
from app.routers.marketing_guide import router as marketing_guide_router
from app.core.config import settings
from app.routers.carteira import router as carteira_router
from app.routers.carteira_import import router as carteira_import_router
from app.routers.lances import router as lances_router
from app.routers.comissoes import router as comissoes_router
from app.routers.auth_debug import router as auth_debug_router
from app.routers.partner_users import router as partner_users_router
from app.routers.contract_documents import router as contract_documents_router
from app.routers.partner_portal import router as partner_portal_router
from app.routers.meta import router as meta_router
from app.routers.whatsapp import router as whatsapp_router
from app.routers.financeiro import router as financeiro_router

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
)

origins = [
    "http://localhost:3000",                # dev
    "https://app.autentika.com.br",         # ajustar para o domínio real
    "https://contemplahub.vercel.app",      # vercel
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
@app.get("", include_in_schema=False)
def root():
    return {"message": "backend no ar"}


# Routers
app.include_router(lead_propostas_router)
app.include_router(lead_cadastros_router)
app.include_router(diagnostic_router)
app.include_router(health_router)
app.include_router(leads_router)
app.include_router(kanban_router)
app.include_router(contracts_router)
app.include_router(marketing_guide_router)
app.include_router(marketing_guide.router)
app.include_router(marketing_guide_pdf.router)
app.include_router(carteira_router)
app.include_router(carteira_import_router)
app.include_router(lances_router)
app.include_router(comissoes_router)
app.include_router(partner_users_router)
app.include_router(auth_debug_router)
app.include_router(contract_documents_router)
app.include_router(partner_portal_router)
app.include_router(meta_router)
app.include_router(whatsapp_router)
app.include_router(financeiro_router)

@app.on_event("startup")
async def print_routes():
    print("=== ROTAS REGISTRADAS ===")
    for route in app.routes:
        if isinstance(route, APIRoute):
            print(f"{sorted(route.methods)} -> {route.path}")
    print("=========================")


_wa_logger = logging.getLogger("whatsapp.scheduler")


async def _whatsapp_dispatch_loop():
    """Agendador embutido: drena a fila de WhatsApp periodicamente."""
    from app.services import whatsapp_service as wa

    interval = settings.WHATSAPP_DISPATCH_INTERVAL_SEC
    while True:
        try:
            supa = get_supabase_admin()
            result = await asyncio.to_thread(wa.process_outbound_queue, supa=supa, limit=25)
            if result.get("processed"):
                _wa_logger.info("whatsapp_dispatch_tick", extra={"result": result})
        except Exception as exc:  # noqa: BLE001
            _wa_logger.warning("whatsapp_dispatch_loop_error", extra={"error": str(exc)})
        await asyncio.sleep(max(interval, 15))


@app.on_event("startup")
async def start_whatsapp_scheduler():
    if settings.WHATSAPP_DISPATCH_INTERVAL_SEC and settings.WHATSAPP_DISPATCH_INTERVAL_SEC > 0:
        app.state._wa_task = asyncio.create_task(_whatsapp_dispatch_loop())
        print(f"[whatsapp] agendador embutido ativo (a cada {settings.WHATSAPP_DISPATCH_INTERVAL_SEC}s)")
    else:
        print("[whatsapp] agendador embutido desligado (WHATSAPP_DISPATCH_INTERVAL_SEC=0)")


@app.on_event("shutdown")
async def stop_whatsapp_scheduler():
    task = getattr(app.state, "_wa_task", None)
    if task:
        task.cancel()
