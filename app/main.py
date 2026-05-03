"""Aplicación FastAPI principal - ViveroOnline.com.co"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes import auth as auth_routes
from app.routes import catalogo as catalogo_routes
from app.routes import marketplace as marketplace_routes
from app.routes import transacciones as transacciones_routes
from app.routes import chat as chat_routes
from app.routes import kpis as kpis_routes
from app.routes import whatsapp as whatsapp_routes
from app.routes import pagos as pagos_routes
from app.routes import public as public_routes
from app.routes import inversores as inversores_routes
from app.routes import pages as pages_routes


settings = get_settings()
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="ViveroOnline API",
    description="Marketplace B2B AgTech para la Sabana de Bogotá",
    version="0.1.0",
    docs_url="/api/docs" if not settings.is_production else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if not settings.is_production else None,
)

# Archivos estáticos (JS, CSS, imágenes del proyecto)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# CORS - dominios permitidos
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.app_base_url,
        "https://vivero-online-j3gi.vercel.app",
        "http://localhost:3000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Montar /static/ para CSS, JS, imágenes
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─────────────────── HEALTH ───────────────────

@app.get("/api/health", tags=["system"])
async def health():
    return {
        "ok": True,
        "service": "vivero-online",
        "version": "0.1.0",
        "env": settings.env,
    }


# ─────────────────── ROUTERS ───────────────────

# API endpoints
app.include_router(auth_routes.router)
app.include_router(catalogo_routes.router)
app.include_router(marketplace_routes.router)
app.include_router(transacciones_routes.router)
app.include_router(chat_routes.router)
app.include_router(kpis_routes.router)
app.include_router(whatsapp_routes.router)
app.include_router(pagos_routes.router)
app.include_router(public_routes.router)
app.include_router(inversores_routes.router)

# HTML pages (deben ir al final para no capturar /api/*)
app.include_router(pages_routes.router)


# ─────────────────── ERROR HANDLER ───────────────────

@app.exception_handler(Exception)
async def unhandled_error(request, exc: Exception):
    """Fallback para que nunca devolvamos un 500 sin contexto."""
    return JSONResponse(
        status_code=500,
        content={"ok": False, "detail": str(exc)[:200]},
    )
