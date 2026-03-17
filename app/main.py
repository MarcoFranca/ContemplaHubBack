# app/main.py
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware

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
from app.routers.lances import router as lances_router
from app.routers.comissoes import router as comissoes_router

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
app.include_router(lances_router)
app.include_router(comissoes_router)

@app.on_event("startup")
async def print_routes():
    print("=== ROTAS REGISTRADAS ===")
    for route in app.routes:
        if isinstance(route, APIRoute):
            print(f"{sorted(route.methods)} -> {route.path}")
    print("=========================")
