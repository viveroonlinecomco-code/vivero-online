"""
WhatsApp Entrega Trigger - Recibe foto de entrega
Rama: feat/payouts-60-40

FLUJO:
1. Viverista sube foto de entrega EN APP (no por WhatsApp)
2. Sistema recibe POST /api/entregas/{entrega_id}/foto-entrega
3. Sube foto a BD
4. Marca estado = 'entregado'
5. Guarda timestamp_entrega (inicia contador 24h automático)
6. CRON cada 1h verificará si 24h + sin reclamo = Payout 40%

INTEGRACIÓN:
- Endpoint: POST /api/entregas/{entrega_id}/foto-entrega (en app/routes/entregas.py)
- Base de datos: Supabase (tabla entregas)
- Fotos: URL almacenada en BD
- Contador: timestamp_entrega para validación

NOTA: Sistema automático, viverista solo sube foto en app
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Form, UploadFile, File
from pydantic import BaseModel

from app.auth.deps import UserContext, require_user
from app.services.supabase import admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/entregas", tags=["entregas"])


class FotoEntregaRequest(BaseModel):
    """Request para subir foto de entrega"""
    entrega_id: int
    foto_url: Optional[str] = None


async def procesar_foto_entrega(
    entrega_id: int,
    foto_url: str,
    user: Optional[UserContext] = None,
) -> dict:
    """
    Procesa foto de entrega y marca como entregado
    
    Llamado desde: endpoint /api/entregas/{entrega_id}/foto-entrega
    
    Args:
        entrega_id: ID de la entrega
        foto_url: URL de la foto (descargada por frontend)
        user: Context del usuario (viverista)
    
    Returns:
        {
            "ok": bool,
            "entrega_id": int,
            "estado": str,
            "timestamp_entrega": datetime,
            "error": str (si error)
        }
    """
    
    db = admin()
    
    try:
        logger.info(f"[ENTREGA] Procesando foto: entrega={entrega_id}, foto={foto_url[:50]}")
        
        # 1. OBTENER DATOS DE LA ENTREGA ACTUAL
        try:
            entrega_result = db.table("entregas").select("*").eq(
                "entrega_id", entrega_id
            ).single().execute()
            
            if not entrega_result.data:
                logger.error(f"[ENTREGA] Entrega {entrega_id} no encontrada")
                return {
                    "ok": False,
                    "error": "entrega_not_found",
                    "mensaje": "Entrega no existe"
                }
            
            entrega = entrega_result.data
            cotizacion_id = entrega.get("cotizacion_id")
            vivero_id = entrega.get("vivero_id")
            estado_actual = entrega.get("estado_entrega")
            
        except Exception as e:
            logger.error(f"[ENTREGA] Error obteniendo entrega: {str(e)}")
            return {
                "ok": False,
                "error": "fetch_error",
                "mensaje": f"Error obteniendo entrega: {str(e)}"
            }
        
        # 2. VALIDAR ESTADO (debe estar en "despachado" o NULL)
        if estado_actual and estado_actual not in ["despachado", "en_camino"]:
            logger.warning(
                f"[ENTREGA] Estado inválido para actualizar: {estado_actual}. "
                f"Solo se puede actualizar desde 'despachado'"
            )
            return {
                "ok": False,
                "error": "invalid_state",
                "mensaje": f"No se puede actualizar entrega en estado '{estado_actual}'"
            }
        
        # 3. GENERAR TIMESTAMP DE ENTREGA (CRÍTICO PARA CONTADOR 24h)
        timestamp_entrega = datetime.utcnow().isoformat()
        
        # 4. ACTUALIZAR ENTREGA A "ENTREGADO"
        try:
            db.table("entregas").update({
                "estado_entrega": "entregado",
                "fecha_entrega": timestamp_entrega,
                "foto_entrega": foto_url,
                "timestamp_entrega": timestamp_entrega,
                "garantia_inicia": timestamp_entrega,  # Inicia contador 24h
                "fecha_actualizacion": datetime.utcnow().isoformat()
            }).eq("entrega_id", entrega_id).execute()
            
            logger.info(
                f"[ENTREGA] Entrega {entrega_id} marcada como ENTREGADO "
                f"(timestamp={timestamp_entrega})"
            )
            
        except Exception as e:
            logger.error(f"[ENTREGA] Error actualizando entrega: {str(e)}")
            return {
                "ok": False,
                "error": "update_failed",
                "mensaje": f"Error actualizando entrega: {str(e)}"
            }
        
        # 5. REGISTRAR EVENTO EN ticket_eventos_whatsapp
        try:
            db.table("ticket_eventos_whatsapp").insert({
                "ticket_id": entrega_id,  # Usar entrega_id como referencia
                "whatsapp_numero": None,  # Foto subida por app, no WhatsApp
                "tipo_evento": "entrega_confirmada",
                "estado": "procesado",
                "json_response": f"Foto entrega confirmada a las {timestamp_entrega}",
                "fecha_creacion": datetime.utcnow().isoformat()
            }).execute()
            
            logger.info(f"[ENTREGA] Evento 'entrega_confirmada' registrado")
            
        except Exception as e:
            logger.error(f"[ENTREGA] Error registrando evento: {str(e)}")
            # No es bloqueante
        
        # 6. TODO: NOTIFICAR CLIENTE POR WhatsApp
        logger.info(
            f"[ENTREGA] TODO: Notificar cliente sobre entrega: "
            f"'Tu pedido ha sido entregado. Tienes 24h para revisar'"
        )
        
        # 7. TODO: NOTIFICAR VIVERISTA
        logger.info(
            f"[ENTREGA] TODO: Notificar viverista sobre foto confirmada: "
            f"'Tu payout 40% está en garantía. Se ejecutará en 24h si sin reclamo'"
        )
        
        # 8. RESPUESTA EXITOSA
        return {
            "ok": True,
            "entrega_id": entrega_id,
            "estado": "entregado",
            "timestamp_entrega": timestamp_entrega,
            "garantia_expira": None,  # Calcular cuando se consuma
            "mensaje": "Entrega confirmada. Contador de 24h iniciado."
        }
        
    except Exception as e:
        logger.error(f"[ENTREGA] Error general: {str(e)}", exc_info=True)
        return {
            "ok": False,
            "error": "general_error",
            "mensaje": str(e)
        }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT FASTAPI
# ═══════════════════════════════════════════════════════════════

@router.post("/{entrega_id}/foto-entrega")
async def upload_foto_entrega(
    entrega_id: int,
    foto_url: str = Form(...),
    user: UserContext = Depends(require_user),
):
    """
    Endpoint para que viverista suba foto de entrega
    
    Args:
        entrega_id: ID de la entrega
        foto_url: URL de la foto (subida previamente por frontend)
        user: Usuario autenticado (debe ser el viverista)
    
    Returns:
        Resultado de procesar_foto_entrega
    """
    
    logger.info(f"[ENDPOINT] POST /entregas/{entrega_id}/foto-entrega - user={user.id}")
    
    # Validar que viverista sea dueño de la entrega
    db = admin()
    try:
        entrega = db.table("entregas").select("vivero_id").eq(
            "entrega_id", entrega_id
        ).single().execute()
        
        if not entrega.data:
            raise HTTPException(404, "Entrega no existe")
        
        # TODO: Validar que user.vivero_id == entrega.vivero_id
        # (cuando sistema de autenticación esté completamente integrado)
        
    except Exception as e:
        logger.error(f"[ENDPOINT] Error validando entrega: {str(e)}")
        raise HTTPException(400, f"Error validando entrega: {str(e)}")
    
    # Procesar foto
    result = await procesar_foto_entrega(
        entrega_id=entrega_id,
        foto_url=foto_url,
        user=user
    )
    
    if not result.get("ok"):
        raise HTTPException(400, result.get("mensaje", "Error procesando foto"))
    
    return result


@router.get("/{entrega_id}/status")
async def get_status_entrega(
    entrega_id: int,
    user: UserContext = Depends(require_user),
):
    """
    Obtiene estado actual de una entrega
    
    Útil para viverista verificar si su foto fue procesada
    y cuándo vence la garantía 24h
    """
    
    db = admin()
    
    try:
        entrega = db.table("entregas").select(
            "entrega_id, estado_entrega, timestamp_entrega, "
            "foto_despacho, foto_entrega"
        ).eq("entrega_id", entrega_id).single().execute()
        
        if not entrega.data:
            raise HTTPException(404, "Entrega no encontrada")
        
        data = entrega.data
        
        # Calcular tiempo hasta garantía vence (si está entregado)
        garantia_vence = None
        if data.get("timestamp_entrega"):
            from datetime import timedelta
            ts = datetime.fromisoformat(data["timestamp_entrega"])
            garantia_vence = (ts + timedelta(hours=24)).isoformat()
        
        return {
            "entrega_id": data["entrega_id"],
            "estado": data["estado_entrega"],
            "timestamp_entrega": data["timestamp_entrega"],
            "garantia_vence": garantia_vence,
            "tiene_foto_despacho": bool(data.get("foto_despacho")),
            "tiene_foto_entrega": bool(data.get("foto_entrega")),
        }
        
    except Exception as e:
        logger.error(f"[ENDPOINT] Error obteniendo status: {str(e)}")
        raise HTTPException(500, f"Error obteniendo status: {str(e)}")
