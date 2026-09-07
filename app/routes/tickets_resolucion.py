"""
Tickets Resolución Handler - Resuelve reclamos
Rama: feat/payouts-60-40

FLUJO:
1. Viverista responde reclamo en app
2. Selecciona resolución:
   - "Te envío reemplazo" → viverista gana, libera Payout 40%
   - "No es mi culpa" → validar Ley 1480:
     * Si producto PERECEDERO → viverista RETIENE 40%
     * Si NO perecedero → refund cliente, viverista PIERDE 40%
3. Marcar ticket como resuelto
4. Ejecutar transferencia (o no)

INTEGRACIÓN:
- Endpoint: POST /api/tickets/reclamo/{ticket_id}/resolver
- Base de datos: Supabase (tickets, entregas, transferencias, productos)
- Validación: Ley 1480 (productos perecederos vs no perecederos)

IMPORTANTE:
- Perecederos: plantas, árboles, sustratos
- No perecederos: materas, accesorios
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Form
from pydantic import BaseModel

from app.auth.deps import UserContext, require_user
from app.services.supabase import admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tickets", tags=["reclamos"])


# CATEGORÍAS PERECEDERAS (Ley 1480 art. 47)
CATEGORIAS_PERECEDERAS = {
    "plantas_ornamentales",
    "plantas",
    "árboles",
    "arboles",
    "sustratos",
    "sustrato",
}

CATEGORIAS_NO_PERECEDERAS = {
    "materas",
    "macetas",
    "accesorios",
    "otros",
}


class ResolverReclamoRequest(BaseModel):
    """Request para resolver un reclamo"""
    ticket_id: int
    resolucion: str  # "viverista_gana", "cliente_gana"
    razon_viverista: Optional[str] = None  # Si viverista argumenta
    notas: Optional[str] = None


def _es_producto_perecedero(categoria: Optional[str]) -> bool:
    """
    Determina si un producto es perecedero según Ley 1480 art. 47
    
    Args:
        categoria: Categoría del producto
    
    Returns:
        True si perecedero, False si no
    """
    
    if not categoria:
        # Si no se sabe, asumir perecedero (más seguro para viverista)
        return True
    
    categoria_lower = categoria.lower().strip()
    
    if categoria_lower in CATEGORIAS_PERECEDERAS:
        return True
    elif categoria_lower in CATEGORIAS_NO_PERECEDERAS:
        return False
    else:
        # Por defecto, asumir perecedero si hay duda
        return True


async def resolver_reclamo(
    ticket_id: int,
    resolucion: str,  # "viverista_gana" | "cliente_gana"
    razon_viverista: Optional[str] = None,
    notas: Optional[str] = None,
    user: Optional[UserContext] = None,
) -> dict:
    """
    Resuelve un reclamo abierto
    
    Llamado desde: POST /api/tickets/reclamo/{ticket_id}/resolver
    
    Args:
        ticket_id: ID del reclamo
        resolucion: "viverista_gana" o "cliente_gana"
        razon_viverista: Argumentación del viverista (si aplica)
        notas: Notas de resolución
        user: Usuario autenticado (viverista)
    
    Returns:
        {
            "ok": bool,
            "ticket_id": int,
            "resolucion": str,
            "payout_40_liberado": bool,
            "refund_cliente": bool,
            "razon": str,
            "error": str (si error)
        }
    """
    
    db = admin()
    
    try:
        logger.info(
            f"[RESOLUCIÓN] Resolviendo ticket {ticket_id}: "
            f"resolucion={resolucion}"
        )
        
        # 1. VALIDAR RESOLUCIÓN
        if resolucion not in ["viverista_gana", "cliente_gana"]:
            return {
                "ok": False,
                "error": "invalid_resolution",
                "razon": f"Resolución inválida: {resolucion}"
            }
        
        # 2. OBTENER DATOS DEL TICKET
        try:
            ticket_result = db.table("tickets_soporte").select("*").eq(
                "ticket_id", ticket_id
            ).single().execute()
            
            if not ticket_result.data:
                logger.error(f"[RESOLUCIÓN] Ticket {ticket_id} no encontrado")
                return {
                    "ok": False,
                    "error": "ticket_not_found",
                    "razon": "Ticket no existe"
                }
            
            ticket = ticket_result.data
            
            # Validar que sea RECLAMO y esté ABIERTO
            if ticket.get("ticket_type") != "RECLAMO":
                return {
                    "ok": False,
                    "error": "not_reclamo",
                    "razon": "Este no es un reclamo (es consulta)"
                }
            
            if ticket.get("estado") != "abierto":
                return {
                    "ok": False,
                    "error": "not_open",
                    "razon": f"Reclamo ya está resuelto (estado: {ticket.get('estado')})"
                }
            
            entrega_id = ticket.get("entrega_id")
            pago_id = ticket.get("pago_id")
            epayco_id = ticket.get("epayco_id")
            
        except Exception as e:
            logger.error(f"[RESOLUCIÓN] Error obteniendo ticket: {str(e)}")
            return {
                "ok": False,
                "error": "fetch_ticket_error",
                "razon": str(e)
            }
        
        # 3. OBTENER DATOS DE LA ENTREGA Y PRODUCTO
        categoria_producto = None
        try:
            entrega_result = db.table("entregas").select(
                "vivero_id, cotizacion_id"
            ).eq("entrega_id", entrega_id).single().execute()
            
            if entrega_result.data:
                cotizacion_id = entrega_result.data["cotizacion_id"]
                
                # Obtener producto de la cotización
                cotizacion_result = db.table("cotizaciones").select(
                    "categoria"  # TODO: verificar nombre columna
                ).eq("cotizacion_id", cotizacion_id).single().execute()
                
                if cotizacion_result.data:
                    categoria_producto = cotizacion_result.data.get("categoria")
            
        except Exception as e:
            logger.warning(f"[RESOLUCIÓN] Error obteniendo categoría: {str(e)}")
            # No es bloqueante
        
        # 4. EVALUAR RESOLUCIÓN
        payout_40_liberado = False
        refund_cliente = False
        
        if resolucion == "viverista_gana":
            # VIVERISTA GANA → LIBERAR PAYOUT 40%
            logger.info(f"[RESOLUCIÓN] Viverista gana: liberando Payout 40%")
            payout_40_liberado = True
            
        elif resolucion == "cliente_gana":
            # CLIENTE GANA → VALIDAR LEY 1480
            es_perecedero = _es_producto_perecedero(categoria_producto)
            
            logger.info(
                f"[RESOLUCIÓN] Cliente gana: es_perecedero={es_perecedero}, "
                f"categoria={categoria_producto}"
            )
            
            if es_perecedero:
                # PRODUCTO PERECEDERO → Viverista RETIENE 40% (sin derecho de retracto)
                logger.info(
                    f"[RESOLUCIÓN] Producto perecedero: "
                    f"Viverista RETIENE Payout 40% (Ley 1480 art. 47)"
                )
                payout_40_liberado = True  # Viverista se queda el dinero
                refund_cliente = False
                
            else:
                # PRODUCTO NO PERECEDERO → Refund cliente, viverista PIERDE 40%
                logger.info(
                    f"[RESOLUCIÓN] Producto NO perecedero: "
                    f"Refund cliente, viverista PIERDE Payout 40%"
                )
                payout_40_liberado = False  # Viverista NO recibe
                refund_cliente = True  # Cliente recibe reembolso
        
        # 5. CREAR TRANSFERENCIA PAYOUT 40% (SI CORRESPONDE)
        if payout_40_liberado and pago_id:
            try:
                # Obtener monto del pago
                pagos_result = db.table("pagos").select(
                    "monto_total, vivero_id"
                ).eq("pago_id", pago_id).single().execute()
                
                if pagos_result.data:
                    monto_total = float(pagos_result.data["monto_total"])
                    vivero_id = pagos_result.data.get("vivero_id")
                    
                    # Calcular Payout 40%
                    comision_vo_40 = (monto_total * 0.03) * 0.40
                    monto_viverista_40 = (monto_total * 0.97) * 0.40
                    
                    # Crear transferencia
                    db.table("transferencias_viverista").insert({
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
                        "referencia_banco": f"TR_{pago_id}_RES_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                        "fecha_transferencia": datetime.utcnow().isoformat(),
                    }).execute()
                    
                    logger.info(
                        f"[RESOLUCIÓN] Payout 40% CREADO: "
                        f"pago={pago_id}, monto=${monto_viverista_40:.2f}"
                    )
                    
            except Exception as e:
                logger.error(f"[RESOLUCIÓN] Error creando transferencia: {str(e)}")
                # No es bloqueante, continuar
        
        # 6. ACTUALIZAR TICKET COMO RESUELTO
        try:
            estado_nuevo = f"resuelto_{resolucion}"
            
            db.table("tickets_soporte").update({
                "estado": estado_nuevo,
                "fecha_atencion": datetime.utcnow().isoformat(),
                "notas_admin": notas or f"Resuelto: {resolucion}",
            }).eq("ticket_id", ticket_id).execute()
            
            logger.info(f"[RESOLUCIÓN] Ticket marcado como {estado_nuevo}")
            
        except Exception as e:
            logger.error(f"[RESOLUCIÓN] Error actualizando ticket: {str(e)}")
            return {
                "ok": False,
                "error": "update_error",
                "razon": str(e)
            }
        
        # 7. TODO: NOTIFICAR PARTES
        logger.info(
            f"[RESOLUCIÓN] TODO: Notificar cliente: "
            f"'Reclamo resuelto. {'Reembolso en camino' if refund_cliente else 'Gracias por tu compra'}'"
        )
        
        logger.info(
            f"[RESOLUCIÓN] TODO: Notificar viverista: "
            f"'Reclamo resuelto. {'Payout 40% completado' if payout_40_liberado else 'Refund a cliente'}'"
        )
        
        # 8. RESPUESTA EXITOSA
        razon_final = (
            f"Viverista retiene 40% (producto perecedero, sin derecho de retracto)"
            if (resolucion == "cliente_gana" and _es_producto_perecedero(categoria_producto))
            else f"Refund cliente, viverista pierde 40%"
            if (resolucion == "cliente_gana" and not _es_producto_perecedero(categoria_producto))
            else "Viverista recibe Payout 40%"
        )
        
        return {
            "ok": True,
            "ticket_id": ticket_id,
            "resolucion": resolucion,
            "payout_40_liberado": payout_40_liberado,
            "refund_cliente": refund_cliente,
            "razon": razon_final,
            "mensaje": f"Reclamo resuelto exitosamente"
        }
        
    except Exception as e:
        logger.error(f"[RESOLUCIÓN] Error general: {str(e)}", exc_info=True)
        return {
            "ok": False,
            "error": "general_error",
            "razon": str(e)
        }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT FASTAPI
# ═══════════════════════════════════════════════════════════════

@router.post("/reclamo/{ticket_id}/resolver")
async def endpoint_resolver_reclamo(
    ticket_id: int,
    resolucion: str = Form(...),
    razon_viverista: str = Form(None),
    notas: str = Form(None),
    user: UserContext = Depends(require_user),
):
    """
    Endpoint para que viverista resuelva un reclamo
    
    Args:
        ticket_id: ID del reclamo
        resolucion: "viverista_gana" o "cliente_gana"
        razon_viverista: Argumentación del viverista
        notas: Notas adicionales
        user: Usuario autenticado
    
    Returns:
        Resultado de resolver_reclamo
    """
    
    logger.info(f"[ENDPOINT] POST /tickets/reclamo/{ticket_id}/resolver")
    
    result = await resolver_reclamo(
        ticket_id=ticket_id,
        resolucion=resolucion,
        razon_viverista=razon_viverista,
        notas=notas,
        user=user
    )
    
    if not result.get("ok"):
        raise HTTPException(400, result.get("razon", "Error resolviendo reclamo"))
    
    return result
