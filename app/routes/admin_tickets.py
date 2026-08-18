"""Endpoints admin para gestión tickets — Opción B HYBRID"""

from fastapi import APIRouter, Depends, HTTPException
from app.auth.deps import UserContext, require_admin
from app.services.supabase import admin as db_admin
from datetime import datetime

logger = __import__("logging").getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/ticket/{ticket_id}/confirmar-cierre")
async def confirmar_cierre_ticket(
    ticket_id: int,
    user: UserContext = Depends(require_admin),
):
    """Elena confirma cierre de ticket respondido por bot
    
    Cambios:
    - estado: "pendiente" → "respondido"
    - estado_interno: "respondido_por_bot" → "confirmado_por_admin"
    - fecha_confirmacion: NOW()
    """
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    db = db_admin()
    
    try:
        # Verificar que ticket existe
        ticket_resp = db.table("tickets_soporte").select(
            "ticket_id, estado, estado_interno, descripcion"
        ).eq("ticket_id", ticket_id).execute()
        
        if not ticket_resp.data:
            raise HTTPException(status_code=404, detail=f"Ticket #{ticket_id} no encontrado")
        
        ticket = ticket_resp.data[0]
        
        # Solo permite confirmar si está en estado correcto
        if ticket.get("estado_interno") not in ("respondido_por_bot", "requiere_analisis"):
            raise HTTPException(
                status_code=400,
                detail=f"Ticket ya está en estado: {ticket.get('estado_interno')}"
            )
        
        # Actualizar a CONFIRMADO
        result = db.table("tickets_soporte").update({
            "estado": "respondido",
            "estado_interno": "confirmado_por_admin",
            "fecha_confirmacion": datetime.utcnow().isoformat(),
            "atendido_por": f"admin_{user.email.split('@')[0] if '@' in user.email else 'admin'}",
        }).eq("ticket_id", ticket_id).execute()
        
        logger.info(
            f"✅ Ticket #{ticket_id} cerrado por {user.email} "
            f"({ticket.get('descripcion', '')[:50]})"
        )
        
        return {
            "ticket_id": ticket_id,
            "status": "confirmado",
            "estado": "respondido",
            "estado_interno": "confirmado_por_admin",
            "mensaje": "✅ Ticket cerrado exitosamente",
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error al confirmar cierre de ticket #{ticket_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al procesar: {str(e)}")


@router.get("/ticket/{ticket_id}")
async def obtener_ticket_detalle(
    ticket_id: int,
    user: UserContext = Depends(require_admin),
):
    """Admin obtiene ticket completo (para dashboard)"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    db = db_admin()
    
    ticket_resp = db.table("tickets_soporte").select(
        "ticket_id, whatsapp_numero, nombre, tipo_solicitud, descripcion, "
        "estado, estado_interno, tipo_derivacion, fecha_creacion, fecha_confirmacion, "
        "atendido_por, notas_admin"
    ).eq("ticket_id", ticket_id).execute()
    
    if not ticket_resp.data:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    
    return ticket_resp.data[0]


@router.get("/tickets/pendientes")
async def listar_tickets_pendientes(
    user: UserContext = Depends(require_admin),
):
    """Lista todos los tickets en estado='pendiente' para Elena"""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    db = db_admin()
    
    resp = db.table("tickets_soporte").select(
        "ticket_id, nombre, whatsapp_numero, tipo_solicitud, descripcion, "
        "estado, estado_interno, tipo_derivacion, fecha_creacion, atendido_por"
    ).eq("estado", "pendiente").order("fecha_creacion", desc=True).execute()
    
    return {
        "total": len(resp.data or []),
        "tickets": resp.data or [],
    }
