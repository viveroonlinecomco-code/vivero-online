"""Rutas que sirven páginas HTML.

Estas NO son API endpoints — sirven el frontend directamente.
"""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates


TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["pages"])


def _tpl(name: str) -> FileResponse:
    """Retorna el archivo HTML estático directamente (no necesita Jinja)."""
    path = TEMPLATES_DIR / name
    return FileResponse(path, media_type="text/html")


# ─────────────────── LANDING PÚBLICA ───────────────────

@router.get("/", response_class=HTMLResponse)
async def landing():
    return _tpl("landing.html")


# ─────────────────── AUTH ───────────────────

@router.get("/auth/ingresar", response_class=HTMLResponse)
async def auth_ingresar():
    return _tpl("auth_phone.html")


@router.get("/auth/otp", response_class=HTMLResponse)
async def auth_otp():
    return _tpl("auth_otp.html")


@router.get("/auth/onboarding", response_class=HTMLResponse)
async def auth_onboarding():
    return _tpl("auth_onboarding.html")


# ─────────────────── APP ───────────────────

@router.get("/viverista", response_class=HTMLResponse)
async def app_viverista():
    return _tpl("viverista_dashboard.html")


@router.get("/viverista/identificar", response_class=HTMLResponse)
async def app_viverista_identificar():
    return _tpl("viverista_identificar.html")


@router.get("/marketplace", response_class=HTMLResponse)
async def app_marketplace():
    return _tpl("marketplace.html")


@router.get("/marketplace/producto/{inventario_id}", response_class=HTMLResponse)
async def app_producto(inventario_id: int):
    return _tpl("marketplace_detalle.html")


# Dashboard único del comprador. El subtipo (paisajista, constructora, conjunto,
# empresa, otro) vive en `clientes.tipo_cliente` y se usa solo como metadata
# para personalizar el saludo y segmentar — NO como rol RLS.
@router.get("/comprador", response_class=HTMLResponse)
async def app_comprador():
    return _tpl("comprador_dashboard.html")


@router.get("/pedido/{transaccion_id}", response_class=HTMLResponse)
async def app_pedido(transaccion_id: int):
    return _tpl("pedido_detalle.html")


@router.get("/admin", response_class=HTMLResponse)
async def app_admin():
    return _tpl("admin_dashboard.html")


@router.get("/pagos/resultado", response_class=HTMLResponse)
async def page_pagos_resultado():
    return _tpl("pagos_resultado.html")


@router.get("/inversores", response_class=HTMLResponse)
async def page_inversores():
    return _tpl("inversores.html")


@router.get("/inversores/login", response_class=HTMLResponse)
async def page_inversores_login():
    return _tpl("inversores_login.html")


# ─────────────────── LEGAL ───────────────────

@router.get("/habeas-data", response_class=HTMLResponse)
async def page_habeas():
    return _tpl("habeas_data.html")


@router.get("/terminos", response_class=HTMLResponse)
async def page_terminos():
    # Por ahora reutiliza habeas_data; luego se puede separar
    return _tpl("habeas_data.html")
