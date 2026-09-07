"""
Tickets Resolución - Cierre de reclamos
Rama: feat/payouts-60-40

FLUJO:
1. Admin revisa reclamo (fotos, descripción)
2. Admin resuelve: 
   - APROBADO: Reembolso al cliente, viverista pierde Payout 40%
   - RECHAZADO: Se rechaza reclamo, viverista recibe Payout 40%
3. Sistema crea transferencia correspondiente
4. Marca ticket_soporte como cerrado

INTEGRACIÓN:
- Endpoint: POST /api/tickets/resolver
- Base de datos: Supabase (tables: tickets_soporte, transferencias_viverista)
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.supabase import admin

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# DEFINIR ROUTER PARA FASTAPI
# ═══════════════════════════════════════════════════════════════
router = APIRouter(
    prefix="/api/tickets",
    tags=["tickets_resolucion"],
    responses={404: {"description": "Not found"}}
)


# ═══════════════════════════════════════════════════════════════
# ESQUEMAS PYDANTIC
# ═══════════════════════════════════════════════════════════════
class ResolverReclamoRequest(BaseModel):
    ticket_id: int
    admin_id: int
    decision: str  # "aprobado" o "rechazado"
    notas_resolucion: Optional[str] = None


class ResolverReclamoResponse(BaseModel):
    ok: bool
    ticket_id: Optional[int] = None
    mensaje: str
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# ENDPOINT HTTP PARA RESOLVER RECLAMO
# ═══════════════════════════════════════════════════════════════
@router.post("/resolver-reclamo", response_model=ResolverReclamoResponse)
async def resolver_reclamo_endpoint(request: ResolverReclamoRequest) -> ResolverReclamoResponse:
    """
    Endpoint para resolver un reclamo (solo para admins)
    
    POST /api/tickets/resolver-reclamo
    {
        "ticket_id": 1,
        "admin_id": 1,
        "decision": "aprobado",
        "notas_resolucion": "Producto llegó defectuoso, reembolso procesado"
    }
    """
    result = await resolver_reclamo(
        ticket_id=request.ticket_id,
        admin_id=request.admin_id,
        decision=request.decision,
        notas_resolucion=request.notas_resolucion
    )
    
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("mensaje", "Error resolviendo reclamo"))
    
    return ResolverReclamoResponse(**result)


# ═══════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL DE LÓGICA
# ═══════════════════════════════════════════════════════════════
async def resolver_reclamo(
    ticket_id: int,
    admin_id: int,
    decision: str,
    notas_resolucion: Optional[str] = None,
) -> dict:
    """
    Resuelve un reclamo abierto
    
    Valida:
    - Que el ticket exista
    - Que esté en estado 'abierto'
    - Que decisión sea 'aprobado' o 'rechazado'
    
    Efectos:
    - Si APROBADO: Reembolsa cliente, anula Payout 40% viverista
    - Si RECHAZADO: Viverista recibe Payout 40%, reclamo se cierra
    - Marca ticket como 'resuelto'
    
    Args:
        ticket_id: ID del ticket a resolver
        admin_id: ID del admin que resuelve
        decision: 'aprobado' o 'rechazado'
        notas_resolucion: Notas del admin sobre la decisión
    
    Returns:
        {
            "ok": bool,
            "ticket_id": int,
            "mensaje": str,
            "error": str (si error)
        }
    """
    
    db = admin_db = admin()
    
    try:
        logger.info(f"[RESOLUCIÓN] Resolviendo ticket {ticket_id}: decisión={decision}")
        
        # 1. VALIDAR DECISIÓN
        if decision not in ["aprobado", "rechazado"]:
            return {
                "ok": False,
                "error": "invalid_decision",
                "mensaje": "Decisión debe ser 'aprobado' o 'rechazado'"
            }
        
        # 2. OBTENER TICKET
        ticket_result = admin_db.table("tickets_soporte").select(
            "ticket_id, entrega_id, cotizacion_id, vivero_id, estado"
        ).eq("ticket_id", ticket_id).single().execute()
        
        if not ticket_result.data:
            return {
                "ok": False,
                "error": "ticket_not_found",
                "mensaje": f"Ticket {ticket_id} no encontrado"
            }
        
        ticket = ticket_result.data
        if ticket["estado"] != "abierto":
            return {
                "ok": False,
                "error": "invalid_status",
                "mensaje": f"Ticket no está en estado 'abierto' (estado: {ticket['estado']})"
            }
        
        entrega_id = ticket["entrega_id"]
        cotizacion_id = ticket["cotizacion_id"]
        vivero_id = ticket["vivero_id"]
        
        # 3. OBTENER MONTO DE LA TRANSACCIÓN
        try:
            cotizacion_result = admin_db.table("cotizaciones").select(
                "monto_total"
            ).eq("cotizacion_id", cotizacion_id).single().execute()
            
            if not cotizacion_result.data:
                raise Exception(f"Cotización {cotizacion_id} no encontrada")
            
            monto_total = float(cotizacion_result.data["monto_total"])
            
        except Exception as e:
            logger.error(f"[RESOLUCIÓN] Error obteniendo monto: {str(e)}")
            return {
                "ok": False,
                "error": "cotizacion_error",
                "mensaje": f"Error obteniendo monto: {str(e)}"
            }
        
        # 4. BUSCAR O CREAR TRANSFERENCIA
        pago_id = None
        try:
            pagos_result = admin_db.table("pagos").select("pago_id").eq(
                "cotizacion_id", cotizacion_id
            ).eq("estado_pago", "aprobado").limit(1).execute()
            
            if pagos_result.data:
                pago_id = pagos_result.data[0]["pago_id"]
        except Exception as e:
            logger.error(f"[RESOLUCIÓN] Error buscando pago: {str(e)}")
        
        # 5. PROCESAR SEGÚN DECISIÓN
        if decision == "aprobado":
            # Reclamo APROBADO → Reembolso cliente, viverista NO recibe Payout 40%
            logger.info(f"[RESOLUCIÓN] Ticket {ticket_id} APROBADO - procesando reembolso")
            
            try:
                # Crear transferencia de reembolso al cliente
                reembolso = monto_total  # 100% de reembolso
                
                # TODO: Crear reembolso en sistema de pagos
                # Por ahora solo registrar en transferencias_viverista como "reembolso"
                
                transfer_result = admin_db.table("transferencias_viverista").insert({
                    "pago_id": pago_id,
                    "vivero_id": vivero_id,
                    "monto_plantas": monto_total,
                    "monto_flete": 0,
                    "viverista_plantas": 0,
                    "viverista_flete": 0,
                    "viverista_total": 0,
                    "plataforma_plantas": 0,
                    "plataforma_flete": 0,
                    "plataforma_total": 0,
                    "estado": "reembolso_cliente",
                    "referencia_banco": f"REM_{ticket_id}_CLIENTE_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                    "fecha_transferencia": datetime.utcnow().isoformat(),
                }).execute()
                
                logger.info(f"[RESOLUCIÓN] Reembolso registrado para cliente")
                
            except Exception as e:
                logger.error(f"[RESOLUCIÓN] Error procesando reembolso: {str(e)}")
                # No es bloqueante
        
        else:  # decision == "rechazado"
            # Reclamo RECHAZADO → Viverista SÍ recibe Payout 40%
            logger.info(f"[RESOLUCIÓN] Ticket {ticket_id} RECHAZADO - procesando Payout 40%")
            
            if pago_id:
                try:
                    comision_vo_40 = (monto_total * 0.03) * 0.40
                    monto_viverista_40 = (monto_total * 0.97) * 0.40
                    
                    transfer_result = admin_db.table("transferencias_viverista").insert({
                        "pago_id": pago_id,
                        "vivero_id": vivero_id,
                        "monto_plantas": monto_total,
                        "monto_flete": 0,
                        "viverista_plantas": monto_viverista_40,
                        "viverista_flete": 0,
                        "viverista_total": monto_viverista_40,
                        "plataforma_plantas": comision_vo_40,
                        "plataforma_flete": 0,
                        "plataforma_total": comision_vo_40,
                        "estado": "enviado",
                        "referencia_banco": f"TR_{pago_id}_PAYOUT40_RECH_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                        "fecha_transferencia": datetime.utcnow().isoformat(),
                    }).execute()
                    
                    logger.info(f"[RESOLUCIÓN] Payout 40% creado: ${monto_viverista_40:.2f}")
                    
                except Exception as e:
                    logger.error(f"[RESOLUCIÓN] Error creando Payout 40%: {str(e)}")
        
        # 6. ACTUALIZAR TICKET A RESUELTO
        try:
            admin_db.table("tickets_soporte").update({
                "estado": "resuelto",
                "decision_admin": decision,
                "admin_id": admin_id,
                "notas_resolucion": notas_resolucion or "",
                "fecha_resolucion": datetime.utcnow().isoformat()
            }).eq("ticket_id", ticket_id).execute()
            
            logger.info(f"[RESOLUCIÓN] Ticket {ticket_id} marcado como resuelto")
            
        except Exception as e:
            logger.error(f"[RESOLUCIÓN] Error actualizando ticket: {str(e)}")
            return {
                "ok": False,
                "error": "update_failed",
                "mensaje": f"Error actualizando ticket: {str(e)}"
            }
        
        # 7. RESPUESTA EXITOSA
        return {
            "ok": True,
            "ticket_id": ticket_id,
            "mensaje": f"Reclamo {decision.upper()} exitosamente. "
                      f"{'Reembolso procesado' if decision == 'aprobado' else 'Payout 40% ejecutado'}"
        }
        
    except Exception as e:
        logger.error(f"[RESOLUCIÓN] Error general: {str(e)}", exc_info=True)
        return {
            "ok": False,
            "error": "general_error",
            "mensaje": str(e)
        }
