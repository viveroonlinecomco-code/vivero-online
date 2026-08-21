"""
Endpoints admin para gestión de tickets - ViveroOnline
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.services.supabase import admin as db_admin
from datetime import datetime
import logging
import traceback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/tickets/pendientes")
async def listar_tickets_pendientes():
    logger.info("🎫 tickets/pendientes - INICIO")
    try:
        db = db_admin()
        logger.info("🎫 tickets/pendientes - db_admin() OK")
        resp = (
            db.table("tickets_soporte")
            .select("*")
            .eq("estado", "pendiente")
            .order("fecha_creacion", desc=True)
            .execute()
        )
        logger.info(f"🎫 tickets/pendientes - query OK, {len(resp.data or [])} resultados")
        return JSONResponse({"total": len(resp.data or []), "tickets": resp.data or []})
    except Exception as e:
        logger.error(f"🎫 tickets/pendientes - ERROR: {e}")
        logger.error(traceback.format_exc())
        return JSONResponse({"total": 0, "tickets": [], "error": str(e)})


@router.get("/tickets/respondidos")
async def listar_tickets_respondidos():
    logger.info("🎫 tickets/respondidos - INICIO")
    try:
        db = db_admin()
        resp = (
            db.table("tickets_soporte")
            .select("*")
            .eq("estado", "respondido")
            .order("fecha_confirmacion", desc=True)
            .limit(50)
            .execute()
        )
        logger.info(f"🎫 tickets/respondidos - query OK, {len(resp.data or [])} resultados")
        return JSONResponse({"total": len(resp.data or []), "tickets": resp.data or []})
    except Exception as e:
        logger.error(f"🎫 tickets/respondidos - ERROR: {e}")
        logger.error(traceback.format_exc())
        return JSONResponse({"total": 0, "tickets": []})


@router.get("/ticket/{ticket_id}")
async def obtener_ticket(ticket_id: int):
    try:
        db = db_admin()
        resp = db.table("tickets_soporte").select("*").eq("ticket_id", ticket_id).single().execute()
        return JSONResponse(resp.data or {})
    except Exception as e:
        logger.error(f"Error obteniendo ticket {ticket_id}: {e}")
        return JSONResponse({})


@router.post("/ticket/{ticket_id}/responder-whatsapp")
async def responder_whatsapp(ticket_id: int, payload: dict):
    try:
        db = db_admin()
        mensaje = payload.get("mensaje", "")
        db.table("tickets_soporte").update({
            "estado": "respondido",
            "atendido_por": "admin_elena",
            "fecha_confirmacion": datetime.utcnow().isoformat(),
            "notas_admin": mensaje,
        }).eq("ticket_id", ticket_id).execute()
        return JSONResponse({"status": "ok", "ticket_id": ticket_id})
    except Exception as e:
        logger.error(f"Error respondiendo ticket {ticket_id}: {e}")
        return JSONResponse({"status": "error", "error": str(e)})


@router.post("/ticket/{ticket_id}/guardar-notas")
async def guardar_notas(ticket_id: int, payload: dict):
    try:
        db = db_admin()
        notas = payload.get("notas", "")
        db.table("tickets_soporte").update({"notas_admin": notas}).eq("ticket_id", ticket_id).execute()
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logger.error(f"Error guardando notas {ticket_id}: {e}")
        return JSONResponse({"status": "error"})
