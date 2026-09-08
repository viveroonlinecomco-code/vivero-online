"""Aplicación FastAPI principal - ViveroOnline.com.co"""
import logging
from collections import defaultdict
from pathlib import Path
from time import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.routes import auth as auth_routes
from app.routes import catalogo as catalogo_routes
from app.routes import viveros as viveros_routes
from app.routes import marketplace as marketplace_routes
from app.routes import transacciones as transacciones_routes
from app.routes import chat as chat_routes
from app.routes import kpis as kpis_routes
from app.routes import whatsapp as whatsapp_routes
from app.routes import pagos as pagos_routes
from app.routes import public as public_routes
from app.routes import ingesta as ingesta_routes
from app.routes import inteligencia as inteligencia_routes
from app.routes import suscripcion as suscripcion_routes
from app.routes import pages as pages_routes
from app.routes.admin_ops import router as admin_ops_router
from app.routes.admin_config import router as admin_config_router
from app.routes.admin_tickets import router as admin_tickets_router
from app.routes.fintech import router as fintech_router
from app.routes.checkout_guest import router as checkout_guest_router
from app.routes.pedidos import router as pedidos_router
from app.routes.suscripcion import router as suscripciones_router
from app.services.google_ads_middleware import GoogleAdsMiddleware
from app.routes import onboarding as onboarding_routes
from app.routes.webhooks_meta import router as webhooks_router

# ═══════════════════════════════════════════════════════════════
# IMPORTS NUEVOS - feat/payouts-60-40
# ═══════════════════════════════════════════════════════════════
from app.routes import epayco_webhook as epayco_webhook_routes
from app.routes import whatsapp_despacho_trigger as despacho_routes
from app.routes import whatsapp_entrega_trigger as entrega_routes
from app.routes import tickets_reclamo as tickets_reclamo_routes
from app.routes import tickets_resolucion as tickets_resolucion_routes

from app.routes.garantia_cron import ejecutar_garantia_cron

logger = logging.getLogger(__name__)

settings = get_settings()
STATIC_DIR = Path(__file__).parent / "static"

# ═══════════════════════════════════════════════════════════════
# SCHEDULER PARA CRON - feat/payouts-60-40
# ═══════════════════════════════════════════════════════════════
_scheduler = AsyncIOScheduler()

async def startup_scheduler():
    """Inicia scheduler al arrancar FastAPI"""
    logger.info("📅 Iniciando scheduler CRON...")
    
    _scheduler.add_job(
        ejecutar_garantia_cron,
        "interval",
        hours=1,
        id="garantia_cron_24h",
        name="Verificación de garantía 24h y Payout 40%",
        max_instances=1,
        misfire_grace_time=60,
    )
    
    _scheduler.start()
    logger.info("✅ Scheduler iniciado")

async def shutdown_scheduler():
    """Detiene scheduler al apagar FastAPI"""
    logger.info("📅 Deteniendo scheduler...")
    _scheduler.shutdown()
    logger.info("✅ Scheduler detenido")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestiona startup y shutdown del app"""
    # Startup
    await startup_scheduler()
    yield
    # Shutdown
    await shutdown_scheduler()

# ═══════════════════════════════════════════════════════════════
# CREACIÓN DE APP
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="ViveroOnline API",
    description="Marketplace B2B AgTech para la Sabana de Bogotá",
    version="0.1.0",
    docs_url="/api/docs" if not settings.is_production else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if not settings.is_production else None,
    lifespan=lifespan,  # ← NUEVO: lifecycle management para scheduler
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    
app.add_middleware(GoogleAdsMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.app_base_url,
        "https://vivero-online-j3gi.vercel.app",
        "https://www.viveroonline.com.co",
        "https://viveroonline.com.co",
        "http://localhost:3000",
        "http://localhost:8000",
    ],
    allow_origin_regex=r"https://vivero-online(-ia)?.*-elenas-projects-0d05ec06\.vercel\.app",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=600,
)

RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/api/auth/otp/send":             (5, 60),
    "/api/auth/otp/verify":           (10, 60),
    "/api/suscripcion/iniciar-pago":  (5, 60),
    "/api/pagos/webhook/epayco":      (30, 60),
}

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_last_cleanup = [time()]


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    limit = RATE_LIMITS.get(request.url.path)
    if limit is None:
        return await call_next(request)

    max_req, window = limit
    ip = _client_ip(request)
    key = f"{ip}:{request.url.path}"
    now = time()

    _rate_buckets[key] = [t for t in _rate_buckets[key] if now - t < window]

    if now - _last_cleanup[0] > 300:
        for k in list(_rate_buckets.keys()):
            if not _rate_buckets[k] or now - _rate_buckets[k][-1] > 600:
                _rate_buckets.pop(k, None)
        _last_cleanup[0] = now

    if len(_rate_buckets[key]) >= max_req:
        retry_after = max(1, int(window - (now - _rate_buckets[key][0])))
        return JSONResponse(
            status_code=429,
            content={"ok": False, "detail": "Demasiadas solicitudes. Esperá un momento e intentá de nuevo."},
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(max_req),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(now + retry_after)),
            },
        )

    _rate_buckets[key].append(now)
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(max_req)
    response.headers["X-RateLimit-Remaining"] = str(max_req - len(_rate_buckets[key]))
    return response


@app.get("/api/health", tags=["system"])
async def health():
    return {"ok": True, "service": "vivero-online", "version": "0.1.0", "env": settings.env}


# ─────────────────── ROUTERS ───────────────────
app.include_router(auth_routes.router)
app.include_router(catalogo_routes.router)
app.include_router(viveros_routes.router)
app.include_router(marketplace_routes.router)
app.include_router(marketplace_routes.public_router)
app.include_router(transacciones_routes.router)
app.include_router(chat_routes.router)
app.include_router(kpis_routes.router)
app.include_router(whatsapp_routes.router)
app.include_router(inteligencia_routes.router)
app.include_router(suscripcion_routes.router)
app.include_router(pagos_routes.router)
app.include_router(public_routes.router)
app.include_router(ingesta_routes.router)
app.include_router(admin_ops_router)
app.include_router(admin_config_router)
app.include_router(admin_tickets_router)
app.include_router(fintech_router)
app.include_router(pedidos_router)
app.include_router(checkout_guest_router)
app.include_router(onboarding_routes.router)
app.include_router(webhooks_router)

# ═══════════════════════════════════════════════════════════════
# ROUTERS NUEVOS - feat/payouts-60-40
# ═══════════════════════════════════════════════════════════════
app.include_router(epayco_webhook_routes.router)
app.include_router(despacho_routes.router)
app.include_router(entrega_routes.router)
app.include_router(tickets_reclamo_routes.router)
app.include_router(tickets_resolucion_routes.router)

logger.info("✅ Routers registrados: epayco_webhook, despacho, entrega, tickets_reclamo, tickets_resolucion")

# HTML pages (deben ir al final para no capturar /api/*)
app.include_router(pages_routes.router)


@app.exception_handler(Exception)
async def unhandled_error(request, exc: Exception):
    return JSONResponse(status_code=500, content={"ok": False, "detail": str(exc)[:200]})
