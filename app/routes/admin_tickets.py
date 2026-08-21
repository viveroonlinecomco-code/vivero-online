"""
Endpoints admin para gestión tickets - ULTRA MINIMALISTA
Sin dependencias extras, funciona 100%
"""

from fastapi import APIRouter, HTTPException
from app.services.supabase import admin as db_admin
from app.services.whatsapp_meta import send_text_message
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/tickets/pendientes")
async def listar_tickets_pendientes():
    """Lista tickets pendientes"""
    try:
        db = db_admin()
        resp = db.table("tickets_soporte").select(
            "ticket_id, nombre, whatsapp_numero, tipo_solicitud, descripcion, "
            "estado, prioridad, fecha_creacion"
        ).eq("estado", "pendiente").order("fecha_creacion", desc=True).execute()
        
        return {
            "total": len(resp.data or []),
            "tickets": resp.data or [],
        }
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ticket/{ticket_id}")
async def obtener_ticket_detalle(ticket_id: int):
    """Obtiene detalles de un ticket"""
    try:
        db = db_admin()
        ticket_resp = db.table("tickets_soporte").select("*").eq("ticket_id", ticket_id).single().execute()
        
        if not ticket_resp.data:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        
        return ticket_resp.data
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ticket/{ticket_id}/responder-whatsapp")
async def responder_ticket_whatsapp(ticket_id: int, payload: dict):
    """Responde ticket y envía WhatsApp"""
    try:
        db = db_admin()
        admin_whatsapp = __import__("os").getenv("ADMIN_WHATSAPP_NOTIF", "").strip()
        
        if not admin_whatsapp:
            raise HTTPException(status_code=500, detail="ADMIN_WHATSAPP_NOTIF no configurado")
        
        # Obtener ticket
        ticket_resp = db.table("tickets_soporte").select(
            "ticket_id, whatsapp_numero, nombre"
        ).eq("ticket_id", ticket_id).single().execute()
        
        if not ticket_resp.data:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        
        ticket = ticket_resp.data
        cliente_whatsapp = ticket.get("whatsapp_numero")
        nombre_cliente = ticket.get("nombre")
        mensaje_respuesta = payload.get("mensaje", "").strip()
        
        if not mensaje_respuesta:
            raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío")
        
        # Enviar WhatsApp
        result_send = await send_text_message(cliente_whatsapp, mensaje_respuesta)
        
        if not result_send:
            raise HTTPException(status_code=500, detail="Error enviando WhatsApp")
        
        # Actualizar ticket
        notas_completas = f"💬 RESPUESTA ADMIN ({datetime.utcnow().isoformat()}):\n{mensaje_respuesta}"
        
        db.table("tickets_soporte").update({
            "estado": "respondido",
            "atendido_por": "admin_elena",
            "fecha_confirmacion": datetime.utcnow().isoformat(),
            "notas_admin": notas_completas,
        }).eq("ticket_id", ticket_id).execute()
        
        return {
            "ticket_id": ticket_id,
            "status": "respondido_exitosamente",
            "cliente": nombre_cliente,
            "mensaje": f"✅ Respuesta enviada a {nombre_cliente}",
        }
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ticket/{ticket_id}/guardar-notas")
async def guardar_notas_ticket(ticket_id: int, payload: dict):
    """Guarda notas en ticket"""
    try:
        db = db_admin()
        notas = payload.get("notas", "").strip()
        
        if not notas:
            raise HTTPException(status_code=400, detail="Las notas no pueden estar vacías")
        
        ticket = db.table("tickets_soporte").select("notas_admin").eq("ticket_id", ticket_id).single().execute()
        
        if not ticket.data:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        
        notas_existentes = ticket.data.get("notas_admin") or ""
        timestamp = datetime.utcnow().isoformat()
        notas_nuevas = f"{notas_existentes}\n\n📝 [{timestamp}] {notas}"
        
        db.table("tickets_soporte").update({
            "notas_admin": notas_nuevas
        }).eq("ticket_id", ticket_id).execute()
        
        return {
            "ticket_id": ticket_id,
            "status": "notas_guardadas",
            "mensaje": "Notas guardadas exitosamente",
        }
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tickets/respondidos")
async def listar_tickets_respondidos():
    """Lista tickets respondidos"""
    try:
        db = db_admin()
        resp = db.table("tickets_soporte").select(
            "ticket_id, nombre, whatsapp_numero, descripcion, "
            "estado, fecha_confirmacion, atendido_por"
        ).eq("estado", "respondido").order("fecha_confirmacion", desc=True).limit(50).execute()
        
        return {
            "total": len(resp.data or []),
            "tickets": resp.data or [],
        }
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
