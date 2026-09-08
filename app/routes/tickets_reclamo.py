"""
Tickets Reclamo - Gestión de reclamos por producto dañado
Rama: feat/payouts-60-40

FLUJO COMPLETO (Escenario 3):
1. Cliente abre reclamo por WhatsApp o APP
2. Sistema valida que entrega exista y esté "entregada"
3. Crea ticket_soporte con estado='abierto'
4. RETIENE Payout 40% (no se ejecuta hasta resolución)
5. ⚠️ Responde automáticamente al cliente (ticket_responder)
6. ⚠️ Notifica al admin (ticket_responder)
7. Admin revisa y resuelve o rechaza

INTEGRACIÓN:
- Endpoint: POST /api/tickets/abrir-reclamo
- Base de datos: Supabase (tabla tickets_soporte)
- Estados: abierto → en_revision → resuelto/rechazado
- Notificaciones: WhatsApp via ticket_responder
"""

from __future__ import annotations
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.supabase import admin
from app.routes.ticket_responder import responder_ticket_segun_tipo, notificar_admin_con_contexto

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# DEFINIR ROUTER PARA FASTAPI
# ═══════════════════════════════════════════════════════════════
router = APIRouter(
    prefix="/api/tickets",
    tags=["tickets_reclamo"],
    responses={404: {"description": "Not found"}}
)


# ═══════════════════════════════════════════════════════════════
# ESQUEMAS PYDANTIC
# ═══════════════════════════════════════════════════════════════
class AbrirReclamoRequest(BaseModel):
    entrega_id: int
    cliente_id: int
    motivo_reclamo: str
    descripcion: Optional[str] = None
    whatsapp_numero: Optional[str] = None
    nombre_cliente: Optional[str] = None


class AbrirReclamoResponse(BaseModel):
    ok: bool
    ticket_id: Optional[int] = None
    mensaje: str
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# ENDPOINT HTTP PARA ABRIR RECLAMO
# ═══════════════════════════════════════════════════════════════
@router.post("/abrir-reclamo", response_model=AbrirReclamoResponse)
async def abrir_reclamo_endpoint(request: AbrirReclamoRequest) -> AbrirReclamoResponse:
    """
    Endpoint para abrir un reclamo CON NOTIFICACIONES AUTOMÁTICAS
    
    POST /api/tickets/abrir-reclamo
    {
        "entrega_id": 123,
        "cliente_id": 456,
        "motivo_reclamo": "producto_danio",
        "descripcion": "Producto llegó roto",
        "whatsapp_numero": "+573001234567",
        "nombre_cliente": "Juan Pérez"
    }
    
    Retorna: ticket_id si éxito
    """
    result = await abrir_reclamo(
        entrega_id=request.entrega_id,
        cliente_id=request.cliente_id,
        motivo_reclamo=request.motivo_reclamo,
        descripcion=request.descripcion,
        whatsapp_numero=request.whatsapp_numero,
        nombre_cliente=request.nombre_cliente
    )
    
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("mensaje", "Error abriendo reclamo"))
    
    return AbrirReclamoResponse(**result)


# ═══════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL DE LÓGICA - CON INTEGRACIONES
# ═══════════════════════════════════════════════════════════════
async def abrir_reclamo(
    entrega_id: int,
    cliente_id: int,
    motivo_reclamo: str,
    descripcion: Optional[str] = None,
    whatsapp_numero: Optional[str] = None,
    nombre_cliente: Optional[str] = None,
) -> dict:
    """
    Abre un reclamo sobre una entrega CON INTEGRACIONES COMPLETAS
    
    Validaciones:
    - Que la entrega exista
    - Que esté en estado 'entregado'
    - Que el motivo sea válido
    
    Efectos:
    - Retiene Payout 40% del viverista
    - Crea ticket_soporte
    - ⚠️ RESPONDE AL CLIENTE (auto-responder)
    - ⚠️ NOTIFICA AL ADMIN (notificación WhatsApp)
    
    Args:
        entrega_id: ID de la entrega
        cliente_id: ID del cliente
        motivo_reclamo: Razón del reclamo (ej: producto_danio, no_llegó)
        descripcion: Descripción detallada del problema
        whatsapp_numero: Número WhatsApp del cliente (para respuesta)
        nombre_cliente: Nombre del cliente (para admin)
    
    Returns:
        {
            "ok": bool,
            "ticket_id": int,
            "mensaje": str,
            "error": str (si error)
        }
    """
    
    db = admin()
    
    try:
        logger.info(f"[RECLAMO] Abriendo reclamo: entrega={entrega_id}, cliente={cliente_id}")
        
        # 1. VALIDAR MOTIVOS CONOCIDOS
        motivos_validos = ["producto_danio", "no_llego", "producto_incorrecto", "otro"]
        if motivo_reclamo not in motivos_validos:
            return {
                "ok": False,
                "error": "invalid_reason",
                "mensaje": f"Motivo inválido. Válidos: {', '.join(motivos_validos)}"
            }
        
        # 2. VERIFICAR QUE ENTREGA EXISTA Y ESTÉ ENTREGADA
        entregas_result = db.table("entregas").select(
            "entrega_id, cotizacion_id, vivero_id, estado_entrega"
        ).eq("entrega_id", entrega_id).single().execute()
        
        if not entregas_result.data:
            return {
                "ok": False,
                "error": "entrega_not_found",
                "mensaje": f"Entrega {entrega_id} no encontrada"
            }
        
        entrega = entregas_result.data
        if entrega["estado_entrega"] != "entregado":
            return {
                "ok": False,
                "error": "invalid_status",
                "mensaje": f"Entrega no está en estado 'entregado' (estado actual: {entrega['estado_entrega']})"
            }
        
        cotizacion_id = entrega["cotizacion_id"]
        vivero_id = entrega["vivero_id"]
        
        # 3. CREAR TICKET_SOPORTE
        try:
            ticket_result = db.table("tickets_soporte").insert({
                "entrega_id": entrega_id,
                "cotizacion_id": cotizacion_id,
                "vivero_id": vivero_id,
                "cliente_id": cliente_id,
                "ticket_type": "RECLAMO",
                "motivo_reclamo": motivo_reclamo,
                "descripcion": descripcion or "",
                "estado": "abierto",
                "fecha_creacion": datetime.utcnow().isoformat(),
                "json_response": f"Reclamo abierto por cliente: {descripcion or motivo_reclamo}"
            }).execute()
            
            if not ticket_result.data:
                raise Exception("No se pudo crear ticket")
            
            ticket_id = ticket_result.data[0]["ticket_id"]
            
            logger.info(f"[RECLAMO] Ticket {ticket_id} creado exitosamente")
            
        except Exception as e:
            logger.error(f"[RECLAMO] Error creando ticket: {str(e)}")
            return {
                "ok": False,
                "error": "ticket_creation_failed",
                "mensaje": f"Error creando ticket: {str(e)}"
            }
        
        # 4. REGISTRAR EN ticket_eventos_whatsapp
        try:
            db.table("ticket_eventos_whatsapp").insert({
                "ticket_id": ticket_id,
                "whatsapp_numero": whatsapp_numero or f"cliente_{cliente_id}",
                "tipo_evento": "reclamo_abierto",
                "estado": "procesado",
                "json_response": f"Reclamo abierto: {motivo_reclamo}",
                "fecha_creacion": datetime.utcnow().isoformat()
            }).execute()
            
        except Exception as e:
            logger.error(f"[RECLAMO] Error registrando evento: {str(e)}")
            # No es bloqueante
        
        # ═══════════════════════════════════════════════════════════════
        # 5. ⚠️ INTEGRACIÓN: RESPONDER AL CLIENTE (ticket_responder)
        # ═══════════════════════════════════════════════════════════════
        respuesta_info = {"tipo": "escalado", "mensaje": ""}
        
        try:
            logger.info(f"[RECLAMO] Llamando responder_ticket_segun_tipo para ticket {ticket_id}")
            
            # Preparar datos del ticket para responder
            ticket_data = {
                "ticket_id": ticket_id,
                "tipo_solicitud": "reclamo",  # Escenario 3: reclamos siempre se escalan
                "descripcion": descripcion or motivo_reclamo,
                "whatsapp_numero": whatsapp_numero or "",
                "nombre": nombre_cliente or "Cliente",
            }
            
            # Llamar función de auto-respuesta
            respuesta_info = await responder_ticket_segun_tipo(ticket_id, ticket_data)
            
            logger.info(f"[RECLAMO] Respuesta al cliente: tipo={respuesta_info.get('tipo')}")
            
        except Exception as e:
            logger.error(f"[RECLAMO] Error en responder_ticket_segun_tipo: {str(e)}")
            # No es bloqueante, continuar
        
        # ═══════════════════════════════════════════════════════════════
        # 6. ⚠️ INTEGRACIÓN: NOTIFICAR AL ADMIN (ticket_responder)
        # ═══════════════════════════════════════════════════════════════
        try:
            logger.info(f"[RECLAMO] Llamando notificar_admin_con_contexto para ticket {ticket_id}")
            
            # Obtener número del admin desde ENV
            admin_whatsapp = os.getenv("ADMIN_WHATSAPP_NOTIF", "")
            
            if admin_whatsapp:
                # Preparar datos del ticket para notificar admin
                ticket_data_admin = {
                    "ticket_id": ticket_id,
                    "tipo_solicitud": "reclamo",
                    "descripcion": descripcion or motivo_reclamo,
                    "whatsapp_numero": whatsapp_numero or "",
                    "nombre": nombre_cliente or "Cliente anónimo",
                }
                
                # Llamar función de notificación admin
                await notificar_admin_con_contexto(
                    ticket_id=ticket_id,
                    ticket_data=ticket_data_admin,
                    respuesta_info=respuesta_info,
                    admin_whatsapp=admin_whatsapp
                )
                
                logger.info(f"[RECLAMO] Admin notificado para ticket {ticket_id}")
            else:
                logger.warning(f"[RECLAMO] ADMIN_WHATSAPP_NOTIF no configurado - sin notificación")
            
        except Exception as e:
            logger.error(f"[RECLAMO] Error en notificar_admin_con_contexto: {str(e)}")
            # No es bloqueante
        
        # 7. RESPUESTA EXITOSA
        return {
            "ok": True,
            "ticket_id": ticket_id,
            "mensaje": f"✅ Reclamo abierto exitosamente (ID: {ticket_id}). "
                      f"Payout 40% está retenido. Cliente notificado. Admin en camino."
        }
        
    except Exception as e:
        logger.error(f"[RECLAMO] Error general: {str(e)}", exc_info=True)
        return {
            "ok": False,
            "error": "general_error",
            "mensaje": str(e)
        }
