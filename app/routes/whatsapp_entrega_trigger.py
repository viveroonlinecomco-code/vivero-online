"""
WhatsApp Entrega Trigger - Recibe confirmación de entrega
Rama: feat/payouts-60-40

FLUJO:
1. Cliente/Transportista envía WhatsApp "Entregado" + FOTO del producto
2. Sistema recibe notificación de Meta
3. Descarga foto y la sube a tabla entregas
4. Marca estado = 'entregado'
5. Inicia contador 24h para Payout 40% (ejecutado por garantia_cron)

INTEGRACIÓN:
- Escucha: webhook Meta POST /api/whatsapp/messages
- Base de datos: Supabase (tabla entregas)
- Fotos: almacenadas en BD como URL
- Payout 40%: ejecutado por CRON después de 24h sin reclamos
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.supabase import admin
from app.services.whatsapp_meta import download_media_bytes

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# DEFINIR ROUTER PARA FASTAPI
# ═══════════════════════════════════════════════════════════════
router = APIRouter(
    prefix="/api/whatsapp",
    tags=["whatsapp_entrega"],
    responses={404: {"description": "Not found"}}
)


# ═══════════════════════════════════════════════════════════════
# ESQUEMAS PYDANTIC
# ═══════════════════════════════════════════════════════════════
class EntregaRequest(BaseModel):
    numero_cliente: str
    cotizacion_id: int
    vivero_id: int
    mensaje_texto: Optional[str] = None
    media_id: Optional[str] = None


class EntregaResponse(BaseModel):
    ok: bool
    entrega_id: Optional[int] = None
    mensaje: str
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# ENDPOINT HTTP PARA WEBHOOK DE ENTREGA
# ═══════════════════════════════════════════════════════════════
@router.post("/entrega-trigger", response_model=EntregaResponse)
async def entrega_trigger_endpoint(request: EntregaRequest) -> EntregaResponse:
    """
    Endpoint para procesar confirmación de entrega desde WhatsApp
    
    POST /api/whatsapp/entrega-trigger
    {
        "numero_cliente": "+573001234567",
        "cotizacion_id": 123,
        "vivero_id": 1,
        "mensaje_texto": "Producto entregado",
        "media_id": "wamid.abc123xyz"
    }
    """
    result = await procesar_mensaje_entrega(
        numero_cliente=request.numero_cliente,
        cotizacion_id=request.cotizacion_id,
        vivero_id=request.vivero_id,
        mensaje_texto=request.mensaje_texto,
        media_id=request.media_id
    )
    
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("mensaje", "Error procesando entrega"))
    
    return EntregaResponse(**result)


# ═══════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL DE LÓGICA
# ═══════════════════════════════════════════════════════════════
async def procesar_mensaje_entrega(
    numero_cliente: str,
    cotizacion_id: int,
    vivero_id: int,
    mensaje_texto: Optional[str] = None,
    media_id: Optional[str] = None,
) -> dict:
    """
    Procesa mensaje de entrega del cliente/transportista
    
    Llamado desde: endpoint entrega_trigger_endpoint
    
    Args:
        numero_cliente: Número WhatsApp del cliente
        cotizacion_id: ID de la cotización/transacción
        vivero_id: ID del vivero
        mensaje_texto: Texto opcional del mensaje
        media_id: ID del media (foto) en Meta
    
    Returns:
        {
            "ok": bool,
            "entrega_id": int (si éxito),
            "error": str (si error),
            "mensaje": str
        }
    """
    
    db = admin()
    
    try:
        logger.info(
            f"[ENTREGA] Procesando: vivero={vivero_id}, "
            f"cotizacion={cotizacion_id}, media={media_id}"
        )
        
        # 1. VERIFICAR QUE EXISTA ENTREGAS PARA ESTA COTIZACIÓN
        entregas_result = db.table("entregas").select("entrega_id").eq(
            "cotizacion_id", cotizacion_id
        ).execute()
        
        if not entregas_result.data:
            logger.warning(f"[ENTREGA] No existe entrega para cotizacion {cotizacion_id}")
            return {
                "ok": False,
                "error": "no_entrega_found",
                "mensaje": "Cotización no encontrada en entregas"
            }
        
        entrega_id = entregas_result.data[0]["entrega_id"]
        
        # 2. DESCARGAR FOTO SI EXISTE media_id
        foto_url = None
        if media_id:
            try:
                logger.info(f"[ENTREGA] Descargando media {media_id}...")
                foto_bytes = await download_media_bytes(media_id)
                
                # TODO: Subir a storage (ej: Supabase Storage, AWS S3)
                # Por ahora almacenar URL en BD
                foto_url = f"media:{media_id}"
                
                logger.info(f"[ENTREGA] Foto descargada: {len(foto_bytes)} bytes")
                
            except Exception as e:
                logger.error(f"[ENTREGA] Error descargando media: {str(e)}")
                # No es bloqueante, continuar sin foto
        
        # 3. ACTUALIZAR ENTREGA A "ENTREGADO" + TIMESTAMP
        try:
            db.table("entregas").update({
                "estado_entrega": "entregado",
                "fecha_entrega": datetime.utcnow().isoformat(),
                "foto_entrega": foto_url,
                "timestamp_entrega": datetime.utcnow().isoformat(),
                "notas_cliente": mensaje_texto or "Entrega confirmada por WhatsApp"
            }).eq("entrega_id", entrega_id).execute()
            
            logger.info(f"[ENTREGA] Entrega {entrega_id} marcada como entregada")
            
        except Exception as e:
            logger.error(f"[ENTREGA] Error actualizando entrega: {str(e)}")
            return {
                "ok": False,
                "error": "update_failed",
                "mensaje": f"Error actualizando entrega: {str(e)}"
            }
        
        # 4. REGISTRAR EVENTO EN ticket_eventos_whatsapp
        try:
            db.table("ticket_eventos_whatsapp").insert({
                "ticket_id": entrega_id,
                "whatsapp_numero": numero_cliente,
                "tipo_evento": "entrega_confirmada",
                "estado": "procesado",
                "json_response": f"Entrega confirmada por cliente {numero_cliente}",
                "fecha_creacion": datetime.utcnow().isoformat()
            }).execute()
            
            logger.info(f"[ENTREGA] Evento registrado en BD")
            
        except Exception as e:
            logger.error(f"[ENTREGA] Error registrando evento: {str(e)}")
            # No es bloqueante
        
        # 5. RESPUESTA EXITOSA
        return {
            "ok": True,
            "entrega_id": entrega_id,
            "foto_url": foto_url,
            "mensaje": f"Entrega registrada exitosamente. Payout 40% se ejecutará en 24h si no hay reclamos."
        }
        
    except Exception as e:
        logger.error(f"[ENTREGA] Error general: {str(e)}", exc_info=True)
        return {
            "ok": False,
            "error": "general_error",
            "mensaje": str(e)
        }
