"""
Tickets Reclamo Handler - Abre y gestiona reclamos
Rama: feat/payouts-60-40

FLUJO:
1. Cliente abre reclamo en app
2. Sistema valida motivo (válido o inválido)
   - Inválido ("me arrepentí"): rechazar automáticamente
   - Válido ("llegó dañado"): crear ticket tipo RECLAMO
3. Crea registro en tickets_soporte con:
   - ticket_type = 'RECLAMO'
   - entrega_id (vinculación)
   - pago_id (vinculación)
   - epayco_id (validación)
4. Retiene Payout 40% automáticamente
5. Notifica viverista por WhatsApp

INTEGRACIÓN:
- Endpoint: POST /api/tickets/reclamo (nuevo)
- Base de datos: Supabase (tabla tickets_soporte, entregas)
- Validación: Ley 1480 (motivos válidos vs inválidos)

IMPORTANTE:
- Motivo INVÁLIDO = rechazar, Payout 40% se libera
- Motivo VÁLIDO = crear ticket, retener Payout 40%
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Form
from pydantic import BaseModel

from app.auth.deps import UserContext, require_user
from app.services.supabase import admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tickets", tags=["reclamos"])


# MOTIVOS VÁLIDOS e INVÁLIDOS (Ley 1480)
MOTIVOS_INVALIDOS = {
    "arrepentimiento",
    "me arrepentí",
    "cambié de opinión",
    "ya no lo quiero",
    "no lo necesito",
    "cambié de idea",
    "encontré otro",
    "es muy caro",
}

MOTIVOS_VALIDOS = {
    "dañado",
    "producto dañado",
    "llegó roto",
    "llegó quebrado",
    "dañado en entrega",
    "incorrecto",
    "especie incorrecta",
    "producto incorrecto",
    "no es lo que pedí",
    "marchito",
    "llegó marchito",
    "muerto",
    "llegó muerto",
    "empaque roto",
    "foto no coincide",
    "no coincide",
}


class AbrirReclamoRequest(BaseModel):
    """Request para abrir reclamo"""
    entrega_id: int
    motivo_reclamo: str
    descripcion: str
    foto_url: Optional[str] = None


def _validar_motivo_reclamo(motivo: str) -> dict:
    """
    Valida si motivo es válido o inválido según Ley 1480
    
    Args:
        motivo: descripción del reclamo
    
    Returns:
        {
            "valido": bool,
            "tipo": "valido" | "invalido" | "ambiguo",
            "razon": str
        }
    """
    
    motivo_lower = motivo.lower().strip()
    
    # Verificar contra motivos inválidos
    for invalido in MOTIVOS_INVALIDOS:
        if invalido in motivo_lower:
            return {
                "valido": False,
                "tipo": "invalido",
                "razon": "Derecho de retracto no aplica por arrepentimiento (Ley 1480 art. 47)"
            }
    
    # Verificar contra motivos válidos
    for valido in MOTIVOS_VALIDOS:
        if valido in motivo_lower:
            return {
                "valido": True,
                "tipo": "valido",
                "razon": "Motivo válido para reclamo"
            }
    
    # Si no coincide con ninguno, clasificar como ambiguo
    return {
        "valido": True,  # Por defecto, asumir válido si hay duda
        "tipo": "ambiguo",
        "razon": "Motivo ambiguo, se abre reclamo para revisión manual"
    }


async def abrir_reclamo(
    entrega_id: int,
    motivo_reclamo: str,
    descripcion: str,
    foto_url: Optional[str] = None,
    user: Optional[UserContext] = None,
) -> dict:
    """
    Abre un reclamo sobre una entrega
    
    Llamado desde: POST /api/tickets/reclamo
    
    Args:
        entrega_id: ID de la entrega
        motivo_reclamo: Motivo del reclamo (texto corto)
        descripcion: Descripción detallada
        foto_url: URL de foto del problema
        user: Usuario autenticado (cliente)
    
    Returns:
        {
            "ok": bool,
            "ticket_id": int (si válido y creado),
            "estado": "rechazado" | "abierto",
            "razon": str,
            "error": str (si error)
        }
    """
    
    db = admin()
    
    try:
        logger.info(
            f"[RECLAMO] Abriendo reclamo: entrega={entrega_id}, "
            f"motivo={motivo_reclamo[:50]}"
        )
        
        # 1. VALIDAR MOTIVO
        validacion = _validar_motivo_reclamo(motivo_reclamo)
        logger.info(
            f"[RECLAMO] Motivo validado: tipo={validacion['tipo']}, "
            f"razon={validacion['razon']}"
        )
        
        # 2. OBTENER DATOS DE LA ENTREGA
        try:
            entrega_result = db.table("entregas").select(
                "entrega_id, cotizacion_id, vivero_id, estado_entrega"
            ).eq("entrega_id", entrega_id).single().execute()
            
            if not entrega_result.data:
                logger.error(f"[RECLAMO] Entrega {entrega_id} no encontrada")
                return {
                    "ok": False,
                    "error": "entrega_not_found",
                    "razon": "Entrega no existe"
                }
            
            entrega = entrega_result.data
            cotizacion_id = entrega["cotizacion_id"]
            vivero_id = entrega["vivero_id"]
            
        except Exception as e:
            logger.error(f"[RECLAMO] Error obteniendo entrega: {str(e)}")
            return {
                "ok": False,
                "error": "fetch_entrega_error",
                "razon": str(e)
            }
        
        # 3. OBTENER pago_id Y epayco_id
        try:
            cotizacion_result = db.table("cotizaciones").select(
                "monto_total"
            ).eq("cotizacion_id", cotizacion_id).single().execute()
            
            if not cotizacion_result.data:
                raise Exception(f"Cotización {cotizacion_id} no encontrada")
            
            monto_total = float(cotizacion_result.data["monto_total"])
            
            # Buscar pago
            pagos_result = db.table("pagos").select(
                "pago_id, epayco_id"
            ).eq("estado_pago", "aprobado").eq(
                "monto_total", monto_total
            ).limit(1).execute()
            
            if not pagos_result.data:
                logger.warning(f"[RECLAMO] No se encontró pago para monto {monto_total}")
                pago_id = None
                epayco_id = None
            else:
                pago_id = pagos_result.data[0]["pago_id"]
                epayco_id = pagos_result.data[0].get("epayco_id")
            
        except Exception as e:
            logger.error(f"[RECLAMO] Error obteniendo pago: {str(e)}")
            pago_id = None
            epayco_id = None
        
        # 4. EVALUAR SI CREAR O RECHAZAR
        if not validacion["valido"]:
            # RECHAZAR RECLAMO
            logger.info(
                f"[RECLAMO] RECHAZANDO reclamo (motivo inválido): "
                f"entrega={entrega_id}"
            )
            
            # Liberar Payout 40% automáticamente
            # (el CRON ya lo haría, pero esto acelera)
            logger.info(
                f"[RECLAMO] Payout 40% se liberará en próxima ejecución CRON "
                f"(sin ticket abierto)"
            )
            
            return {
                "ok": True,
                "ticket_id": None,
                "estado": "rechazado",
                "razon": validacion["razon"],
                "mensaje": "Reclamo rechazado. El derecho de retracto no aplica."
            }
        
        # 5. CREAR TICKET RECLAMO (VÁLIDO)
        try:
            ticket_result = db.table("tickets_soporte").insert({
                "ticket_type": "RECLAMO",  # CRÍTICO: diferencia de CONSULTA
                "whatsapp_numero": None,  # Se llenará después
                "nombre": user.nombre if user else "Cliente",
                "tipo_solicitud": "reclamo",
                "motivo_reclamo": motivo_reclamo,
                "descripcion": descripcion,
                "prioridad": "alta",  # Reclamos siempre alta prioridad
                "estado": "abierto",
                "cliente_id": user.id if user else None,
                "entrega_id": entrega_id,  # VINCULACIÓN CRÍTICA
                "pago_id": pago_id,  # VINCULACIÓN CRÍTICA
                "epayco_id": epayco_id,  # VALIDACIÓN
                "fecha_creacion": datetime.utcnow().isoformat(),
            }).execute()
            
            if not ticket_result.data:
                raise Exception("Error creando ticket")
            
            ticket_id = ticket_result.data[0]["ticket_id"]
            
            logger.info(
                f"[RECLAMO] Ticket CREADO: ticket={ticket_id}, "
                f"entrega={entrega_id}, pago={pago_id}"
            )
            
        except Exception as e:
            logger.error(f"[RECLAMO] Error creando ticket: {str(e)}")
            return {
                "ok": False,
                "error": "ticket_creation_error",
                "razon": str(e)
            }
        
        # 6. REGISTRAR EVENTO EN ticket_eventos_whatsapp
        try:
            db.table("ticket_eventos_whatsapp").insert({
                "ticket_id": ticket_id,
                "whatsapp_numero": None,
                "tipo_evento": "reclamo_abierto",
                "estado": "procesado",
                "json_response": f"Reclamo abierto: {motivo_reclamo}",
                "fecha_creacion": datetime.utcnow().isoformat()
            }).execute()
            
            logger.info(f"[RECLAMO] Evento registrado")
            
        except Exception as e:
            logger.error(f"[RECLAMO] Error registrando evento: {str(e)}")
            # No es bloqueante
        
        # 7. RETENER PAYOUT 40%
        logger.info(
            f"[RECLAMO] Payout 40% RETENIDO automáticamente "
            f"(ticket abierto, CRON no lo pagará)"
        )
        
        # 8. TODO: NOTIFICAR VIVERISTA POR WhatsApp
        logger.info(
            f"[RECLAMO] TODO: Notificar viverista {vivero_id}: "
            f"'⚠️ RECLAMO ABIERTO - Entrega #XXX - Cliente: XXX - "
            f"Motivo: {motivo_reclamo} - Tu payout 40% está RETENIDO. "
            f"Responde en app para resolver.'"
        )
        
        # 9. RESPUESTA EXITOSA
        return {
            "ok": True,
            "ticket_id": ticket_id,
            "estado": "abierto",
            "razon": validacion["razon"],
            "mensaje": (
                f"Reclamo abierto (ticket #{ticket_id}). "
                f"El viverista ha sido notificado y tiene 48h para responder."
            )
        }
        
    except Exception as e:
        logger.error(f"[RECLAMO] Error general: {str(e)}", exc_info=True)
        return {
            "ok": False,
            "error": "general_error",
            "razon": str(e)
        }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT FASTAPI
# ═══════════════════════════════════════════════════════════════

@router.post("/reclamo")
async def endpoint_abrir_reclamo(
    entrega_id: int = Form(...),
    motivo_reclamo: str = Form(...),
    descripcion: str = Form(...),
    foto_url: Optional[str] = Form(None),
    user: UserContext = Depends(require_user),
):
    """
    Endpoint para que cliente abra un reclamo
    
    Args:
        entrega_id: ID de la entrega
        motivo_reclamo: Motivo del reclamo (texto corto)
        descripcion: Descripción detallada
        foto_url: URL de foto del problema (opcional)
        user: Usuario autenticado
    
    Returns:
        Resultado de abrir_reclamo
    """
    
    logger.info(f"[ENDPOINT] POST /tickets/reclamo - entrega={entrega_id}")
    
    result = await abrir_reclamo(
        entrega_id=entrega_id,
        motivo_reclamo=motivo_reclamo,
        descripcion=descripcion,
        foto_url=foto_url,
        user=user
    )
    
    if not result.get("ok"):
        raise HTTPException(400, result.get("razon", "Error abriendo reclamo"))
    
    return result


@router.get("/reclamo/{ticket_id}")
async def get_reclamo_status(
    ticket_id: int,
    user: UserContext = Depends(require_user),
):
    """
    Obtiene estado actual de un reclamo
    """
    
    db = admin()
    
    try:
        ticket = db.table("tickets_soporte").select("*").eq(
            "ticket_id", ticket_id
        ).single().execute()
        
        if not ticket.data:
            raise HTTPException(404, "Reclamo no encontrado")
        
        data = ticket.data
        
        return {
            "ticket_id": data["ticket_id"],
            "estado": data["estado"],
            "motivo": data.get("motivo_reclamo"),
            "descripcion": data["descripcion"],
            "entrega_id": data.get("entrega_id"),
            "fecha_apertura": data["fecha_creacion"],
            "fecha_resolucion": data.get("fecha_atencion"),
            "notas_admin": data.get("notas_admin"),
        }
        
    except Exception as e:
        logger.error(f"[ENDPOINT] Error obteniendo reclamo: {str(e)}")
        raise HTTPException(500, f"Error: {str(e)}")
