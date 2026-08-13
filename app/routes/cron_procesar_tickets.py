"""Cron para procesar tickets de soporte sin respuesta.

Se ejecuta cada hora verificando:
1. Tickets "pendiente" sin respuesta hace >2 horas → auto-responder
2. Tickets que requieren derivación → notificar admin

Endpoint: GET /api/cron/procesar-tickets?secret=CRON_SECRET
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.services.supabase import admin as db_admin
from app.routes.ticket_responder import responder_ticket_segun_tipo, notificar_admin_ticket_con_respuesta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cron", tags=["cron"])

ADMIN_WHATSAPP = __import__("os").getenv("ADMIN_WHATSAPP_NOTIF", "").strip()


@router.get("/procesar-tickets")
async def cron_procesar_tickets(request: Request):
    """Procesa tickets sin respuesta hace >2 horas."""
    s = get_settings()
    secret = request.query_params.get("secret", "")
    if secret != s.cron_secret:
        raise HTTPException(403, "Invalid CRON_SECRET")

    db = db_admin()

    # Buscar tickets pendiente sin respuesta hace >2 horas
    ahora = datetime.now(timezone.utc)
    hace_2h = ahora - timedelta(hours=2)
    
    tickets_resp = db.table("tickets_soporte").select(
        "ticket_id, whatsapp_numero, nombre, tipo_solicitud, descripcion, prioridad, estado"
    ).eq("estado", "pendiente").lt("fecha_creacion", hace_2h.isoformat()).execute()

    tickets = tickets_resp.data or []
    procesados = 0
    respondidos = 0
    derivados = 0

    logger.info(f"CRON: Procesando {len(tickets)} tickets sin respuesta...")

    for ticket in tickets:
        ticket_id = ticket.get("ticket_id")
        try:
            # Auto-responder
            respuesta_info = await responder_ticket_segun_tipo(ticket_id, ticket)

            if respuesta_info["respondido"]:
                respondidos += 1
            if respuesta_info["accion"] == "derivar_admin":
                derivados += 1
            
            # Notificar admin si es relevante
            if ADMIN_WHATSAPP:
                await notificar_admin_ticket_con_respuesta(
                    ticket_id, ticket, respuesta_info, ADMIN_WHATSAPP
                )

            procesados += 1

        except Exception as e:
            logger.error(f"Error procesando ticket #{ticket_id}: {e}")

    resultado = {
        "ok": True,
        "tickets_procesados": procesados,
        "respondidos": respondidos,
        "derivados_admin": derivados,
        "total_sin_respuesta": len(tickets),
    }

    logger.info(f"✅ CRON finalizado: {resultado}")
    return resultado


@router.get("/limpiar-tickets-viejos")
async def cron_limpiar_tickets_viejos(request: Request):
    """
    Limpia tickets muy viejos (>30 días) que ya fueron respondidos.
    Solo archiva, no borra.
    """
    s = get_settings()
    secret = request.query_params.get("secret", "")
    if secret != s.cron_secret:
        raise HTTPException(403, "Invalid CRON_SECRET")

    db = db_admin()
    ahora = datetime.now(timezone.utc)
    hace_30d = ahora - timedelta(days=30)

    tickets_resp = db.table("tickets_soporte").select(
        "ticket_id"
    ).eq("estado", "respondido").lt("fecha_atencion", hace_30d.isoformat()).execute()

    tickets = tickets_resp.data or []
    logger.info(f"CRON: Archivando {len(tickets)} tickets respondidos hace >30 días")

    # Marcar como "archivado" en lugar de eliminar
    if tickets:
        for ticket in tickets:
            db.table("tickets_soporte").update({
                "estado": "archivado",
                "notas_admin": "Archivado por antigüedad",
            }).eq("ticket_id", ticket["ticket_id"]).execute()

    return {
        "ok": True,
        "tickets_archivados": len(tickets),
    }
