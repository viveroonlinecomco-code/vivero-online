"""
Endpoints admin para gestión de tickets - ViveroOnline
Versión minimalista y robusta - Sin dependencias complicadas
"""

from fastapi import APIRouter
from app.services.supabase import admin as db_admin
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/tickets/pendientes")
async def listar_tickets_pendientes():
    """Lista todos los tickets en estado pendiente"""
    try:
        db = db_admin()
        resp = db.table("tickets_soporte").select("*").eq("estado", "pendiente").order("fecha_creacion", desc=True).execute()
        return {
            "total": len(resp.data or []),
            "tickets": resp.data or []
        }
    except Exception as e:
        logger.error(f"Error listando pendientes: {e}")
        return {
            "total": 0,
            "tickets": [],
            "error": str(e)
        }


@router.get("/ticket/{ticket_id}")
async def obtener_ticket(ticket_id: int):
    """Obtiene detalles de un ticket específico"""
    try:
        db = db_admin()
        resp = db.table("tickets_soporte").select("*").eq("ticket_id", ticket_id).single().execute()
        return resp.data or {}
    except Exception as e:
        logger.error(f"Error obteniendo ticket {ticket_id}: {e}")
        return {"error": str(e)}


@router.post("/ticket/{ticket_id}/responder-whatsapp")
async def responder_whatsapp(ticket_id: int, payload: dict):
    """Responde un ticket y actualiza su estado"""
    try:
        db = db_admin()
        mensaje = payload.get("mensaje", "")
        
        if not mensaje:
            return {"status": "error", "message": "El mensaje no puede estar vacío"}
        
        db.table("tickets_soporte").update({
            "estado": "respondido",
            "atendido_por": "admin_elena",
            "fecha_confirmacion": datetime.utcnow().isoformat(),
            "notas_admin": mensaje,
        }).eq("ticket_id", ticket_id).execute()
        
        return {
            "status": "ok",
            "ticket_id": ticket_id,
            "message": "Ticket respondido exitosamente"
        }
    except Exception as e:
        logger.error(f"Error respondiendo ticket {ticket_id}: {e}")
        return {
            "status": "error",
            "error": str(e)
        }


@router.post("/ticket/{ticket_id}/guardar-notas")
async def guardar_notas(ticket_id: int, payload: dict):
    """Guarda notas internas en un ticket"""
    try:
        db = db_admin()
        notas = payload.get("notas", "")
        
        if not notas:
            return {"status": "error", "message": "Las notas no pueden estar vacías"}
        
        db.table("tickets_soporte").update({
            "notas_admin": notas
        }).eq("ticket_id", ticket_id).execute()
        
        return {
            "status": "ok",
            "ticket_id": ticket_id,
            "message": "Notas guardadas exitosamente"
        }
    except Exception as e:
        logger.error(f"Error guardando notas en ticket {ticket_id}: {e}")
        return {
            "status": "error",
            "error": str(e)
        }


@router.get("/tickets/respondidos")
async def listar_respondidos():
    """Lista todos los tickets ya respondidos"""
    try:
        db = db_admin()
        resp = db.table("tickets_soporte").select("*").eq("estado", "respondido").order("fecha_confirmacion", desc=True).limit(50).execute()
        return {
            "total": len(resp.data or []),
            "tickets": resp.data or []
        }
    except Exception as e:
        logger.error(f"Error listando respondidos: {e}")
        return {
            "total": 0,
            "tickets": [],
            "error": str(e)
        }
