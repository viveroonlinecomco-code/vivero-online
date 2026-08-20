"""Endpoints admin para gestión tickets — COMPLETO CON RESPUESTA WHATSAPP"""

from fastapi import APIRouter, Depends, HTTPException
from app.auth.deps import UserContext, require_admin
from app.services.supabase import admin as db_admin
from app.services.whatsapp_meta import send_text_message
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/tickets/pendientes")
async def listar_tickets_pendientes(
    user: UserContext = Depends(require_admin),
):
    """Lista todos los tickets en estado='pendiente' para Elena"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    db = db_admin()
    
    try:
        resp = db.table("tickets_soporte").select(
            "ticket_id, nombre, whatsapp_numero, tipo_solicitud, descripcion, "
            "estado, prioridad, fecha_creacion"
        ).eq("estado", "pendiente").order("fecha_creacion", desc=True).execute()
        
        logger.info(f"✅ Listando {len(resp.data or [])} tickets pendientes")
        
        return {
            "total": len(resp.data or []),
            "tickets": resp.data or [],
        }
    except Exception as e:
        logger.error(f"Error listando tickets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ticket/{ticket_id}")
async def obtener_ticket_detalle(
    ticket_id: int,
    user: UserContext = Depends(require_admin),
):
    """Admin obtiene detalles completos del ticket"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    db = db_admin()
    
    try:
        ticket_resp = db.table("tickets_soporte").select(
            "ticket_id, whatsapp_numero, nombre, tipo_solicitud, descripcion, "
            "estado, prioridad, fecha_creacion, notas_admin, atendido_por, fecha_confirmacion"
        ).eq("ticket_id", ticket_id).single().execute()
        
        if not ticket_resp.data:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        
        logger.info(f"✅ Obteniendo detalles ticket #{ticket_id}")
        return ticket_resp.data
    
    except Exception as e:
        logger.error(f"Error obteniendo ticket #{ticket_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ticket/{ticket_id}/responder-whatsapp")
async def responder_ticket_whatsapp(
    ticket_id: int,
    payload: dict,
    user: UserContext = Depends(require_admin),
):
    """✅ FLUJO COMPLETO: Elena responde por WhatsApp + cierra ticket + actualiza BD
    
    Payload:
    {
        "mensaje": "Hola Hannah, hemos cotizado tu proyecto...",
        "notas": "Contactada por viverista X" (opcional)
    }
    
    Acciones:
    1. Envía mensaje al cliente por WhatsApp (desde ADMIN_WHATSAPP)
    2. Actualiza ticket: estado → "respondido"
    3. Registra quién respondió (atendido_por)
    4. Guarda hora de respuesta (fecha_confirmacion)
    5. Guarda notas y mensaje en BD
    """
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    db = db_admin()
    admin_whatsapp = __import__("os").getenv("ADMIN_WHATSAPP_NOTIF", "").strip()
    
    if not admin_whatsapp:
        raise HTTPException(status_code=500, detail="ADMIN_WHATSAPP_NOTIF no configurado")
    
    try:
        # 1. OBTENER TICKET
        ticket_resp = db.table("tickets_soporte").select(
            "ticket_id, whatsapp_numero, nombre, descripcion, estado"
        ).eq("ticket_id", ticket_id).single().execute()
        
        if not ticket_resp.data:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        
        ticket = ticket_resp.data
        cliente_whatsapp = ticket.get("whatsapp_numero")
        nombre_cliente = ticket.get("nombre")
        mensaje_respuesta = payload.get("mensaje", "").strip()
        notas_admin = payload.get("notas", "").strip()
        
        if not mensaje_respuesta:
            raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío")
        
        # 2. ENVIAR MENSAJE POR WHATSAPP (desde admin)
        logger.info(f"📤 Enviando respuesta a {cliente_whatsapp} (Ticket #{ticket_id})")
        
        result_send = await send_text_message(cliente_whatsapp, mensaje_respuesta)
        
        if not result_send:
            logger.error(f"❌ Error enviando WhatsApp a {cliente_whatsapp}")
            raise HTTPException(status_code=500, detail="Error enviando WhatsApp")
        
        logger.info(f"✅ Mensaje enviado exitosamente")
        
        # 3. ACTUALIZAR TICKET EN BD
        admin_email = user.email if hasattr(user, 'email') else "admin"
        admin_name = admin_email.split("@")[0] if "@" in admin_email else admin_email
        
        notas_completas = (
            f"💬 RESPUESTA ADMIN ({datetime.utcnow().isoformat()}):\n"
            f"{mensaje_respuesta}\n\n"
        )
        if notas_admin:
            notas_completas += f"📋 NOTAS:\n{notas_admin}"
        
        update_data = {
            "estado": "respondido",
            "atendido_por": f"admin_{admin_name}",
            "fecha_confirmacion": datetime.utcnow().isoformat(),
            "notas_admin": notas_completas,
        }
        
        update_resp = db.table("tickets_soporte").update(update_data).eq(
            "ticket_id", ticket_id
        ).execute()
        
        logger.info(f"✅ Ticket #{ticket_id} actualizado en BD")
        
        # 4. RESPUESTA AL ADMIN
        return {
            "ticket_id": ticket_id,
            "status": "respondido_exitosamente",
            "cliente": nombre_cliente,
            "whatsapp": cliente_whatsapp,
            "estado_nuevo": "respondido",
            "mensaje": f"✅ Respuesta enviada a {nombre_cliente}",
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error respondiendo ticket #{ticket_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.post("/ticket/{ticket_id}/guardar-notas")
async def guardar_notas_ticket(
    ticket_id: int,
    payload: dict,
    user: UserContext = Depends(require_admin),
):
    """Guarda solo notas sin responder (para seguimiento interno)"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    db = db_admin()
    
    try:
        notas = payload.get("notas", "").strip()
        if not notas:
            raise HTTPException(status_code=400, detail="Las notas no pueden estar vacías")
        
        # Obtener notas actuales
        ticket = db.table("tickets_soporte").select("notas_admin").eq(
            "ticket_id", ticket_id
        ).single().execute()
        
        if not ticket.data:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        
        notas_existentes = ticket.data.get("notas_admin") or ""
        
        # Agregar nuevas notas
        timestamp = datetime.utcnow().isoformat()
        notas_nuevas = f"{notas_existentes}\n\n📝 [{timestamp}] {notas}"
        
        db.table("tickets_soporte").update({
            "notas_admin": notas_nuevas
        }).eq("ticket_id", ticket_id).execute()
        
        logger.info(f"✅ Notas guardadas en ticket #{ticket_id}")
        
        return {
            "ticket_id": ticket_id,
            "status": "notas_guardadas",
            "mensaje": "Notas guardadas exitosamente",
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error guardando notas #{ticket_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tickets/respondidos")
async def listar_tickets_respondidos(
    user: UserContext = Depends(require_admin),
):
    """Lista tickets ya respondidos (para historial)"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    db = db_admin()
    
    try:
        resp = db.table("tickets_soporte").select(
            "ticket_id, nombre, whatsapp_numero, descripcion, "
            "estado, fecha_confirmacion, atendido_por"
        ).eq("estado", "respondido").order("fecha_confirmacion", desc=True).limit(50).execute()
        
        logger.info(f"✅ Listando {len(resp.data or [])} tickets respondidos")
        
        return {
            "total": len(resp.data or []),
            "tickets": resp.data or [],
        }
    except Exception as e:
        logger.error(f"Error listando respondidos: {e}")
        raise HTTPException(status_code=500, detail=str(e))
