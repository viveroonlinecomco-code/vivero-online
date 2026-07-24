"""Rutas HTTP para integración con la fintech B2B.

Diseño:
- Endpoint webhook público (validado por firma dentro del stub)
- Endpoint admin para consultar estado de línea de crédito de un cliente
- Endpoint admin para forzar re-evaluación

Todas las llamadas al proveedor externo pasan por app/services/fintech_stub.py
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.deps import UserContext, require_admin
from app.services.fintech_stub import (
    fintech_esta_activa,
    obtener_partner_actual,
    verificar_linea_credito,
    procesar_webhook_fintech,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fintech", tags=["fintech"])


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT ADMIN — Estado general
# ═══════════════════════════════════════════════════════════════════════

@router.get("/status")
async def obtener_status(user: UserContext = Depends(require_admin)):
    """Consulta el estado actual de la integración fintech.

    Útil para el dashboard admin cuando Elena quiera saber si la fintech
    está activa y qué proveedor está configurado.
    """
    return {
        "ok": True,
        "activa": fintech_esta_activa(),
        "partner": obtener_partner_actual(),
        "mensaje": (
            "Fintech configurada y activa."
            if fintech_esta_activa()
            else "Fintech inactiva. Solo pago inmediato disponible."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT ADMIN — Verificar línea de crédito de un cliente
# ═══════════════════════════════════════════════════════════════════════

class VerificarLineaReq(BaseModel):
    cliente_id: int
    monto_cop: float
    plazo_dias: int = 30


@router.post("/verificar-linea")
async def verificar_linea_credito_endpoint(
    req: VerificarLineaReq,
    user: UserContext = Depends(require_admin),
):
    """Consulta si un cliente tiene línea de crédito suficiente.

    Solo admin puede llamarlo desde el dashboard. En el flujo normal, esto
    se llama internamente desde el checkout.
    """
    if req.plazo_dias not in (30, 60, 90):
        raise HTTPException(400, "plazo_dias debe ser 30, 60 o 90")

    linea = verificar_linea_credito(
        cliente_id=req.cliente_id,
        monto_solicitado_cop=req.monto_cop,
        plazo_dias=req.plazo_dias,
    )

    return {
        "ok": True,
        "cliente_id": linea.cliente_id,
        "aprobada": linea.aprobada,
        "monto_maximo_cop": linea.monto_maximo_cop,
        "monto_disponible_cop": linea.monto_disponible_cop,
        "plazos_disponibles": linea.plazos_disponibles,
        "fecha_evaluacion": linea.fecha_evaluacion.isoformat(),
        "proveedor": linea.proveedor,
    }


# ═══════════════════════════════════════════════════════════════════════
# WEBHOOK PÚBLICO — Notificaciones del proveedor fintech
# ═══════════════════════════════════════════════════════════════════════

@router.post("/webhook")
async def webhook_fintech(request: Request):
    """Endpoint público para webhooks del proveedor fintech.

    IMPORTANTE: la validación de firma HMAC ocurre dentro de
    app/services/fintech_stub.procesar_webhook_fintech() cuando haya
    proveedor real. Este endpoint solo se encarga de:
      1. Extraer el JSON del body
      2. Delegar al procesador
      3. Devolver respuesta neutra

    Mientras fintech_activa=false, este endpoint responde 200 y descarta
    los webhooks (útil por si el proveedor está haciendo pruebas de red).
    """
    try:
        payload = await request.json()
    except Exception as e:
        logger.warning(f"Webhook fintech con payload inválido: {e}")
        # Devolver 200 igual para que el proveedor no reintente indefinidamente
        return {"ok": False, "reason": "invalid_payload"}

    resultado = procesar_webhook_fintech(payload)

    return {
        "ok": resultado.get("ok", False),
        "mensaje": resultado.get("mensaje", ""),
    }
