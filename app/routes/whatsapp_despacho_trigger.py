"""
WhatsApp Despacho Trigger - Recibe notificación de despacho
Rama: feat/payouts-60-40

FLUJO:
1. Viverista envía WhatsApp "Despacho" + FOTO del conductor
2. Sistema recibe notificación de Meta
3. Descarga foto y la sube a tabla entregas
4. Marca estado = 'despachado'
5. EJECUTA: Payout 60%

INTEGRACIÓN:
- Escucha: webhook Meta POST /api/whatsapp/messages
- Base de datos: Supabase (tabla entregas)
- Fotos: almacenadas en BD como URL
- Payout: crea registro en transferencias_viverista
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
    tags=["whatsapp_despacho"],
    responses={404: {"description": "Not found"}}
)


# ═══════════════════════════════════════════════════════════════
# ESQUEMAS PYDANTIC
# ═══════════════════════════════════════════════════════════════
class DespachoRequest(BaseModel):
    numero_viverista: str
    cotizacion_id: int
    vivero_id: int
    mensaje_texto: Optional[str] = None
    media_id: Optional[str] = None


class DespachoResponse(BaseModel):
    ok: bool
    entrega_id: Optional[int] = None
    payout_60_creado: bool = False
    foto_url: Optional[str] = None
    mensaje: str
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# ENDPOINT HTTP PARA WEBHOOK DE DESPACHO
# ═══════════════════════════════════════════════════════════════
@router.post("/despacho-trigger", response_model=DespachoResponse)
async def despacho_trigger_endpoint(request: DespachoRequest) -> DespachoResponse:
    """
    Endpoint para procesar confirmación de despacho desde WhatsApp
    
    POST /api/whatsapp/despacho-trigger
    {
        "numero_viverista": "+573178543819",
        "cotizacion_id": 123,
        "vivero_id": 1,
        "mensaje_texto": "Despacho listo",
        "media_id": "wamid.abc123xyz"
    }
    """
    result = await procesar_mensaje_despacho(
        numero_viverista=request.numero_viverista,
        cotizacion_id=request.cotizacion_id,
        vivero_id=request.vivero_id,
        mensaje_texto=request.mensaje_texto,
        media_id=request.media_id
    )
    
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("mensaje", "Error procesando despacho"))
    
    return DespachoResponse(**result)


# ═══════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL DE LÓGICA
# ═══════════════════════════════════════════════════════════════
async def procesar_mensaje_despacho(
    numero_viverista: str,
    cotizacion_id: int,
    vivero_id: int,
    mensaje_texto: Optional[str] = None,
    media_id: Optional[str] = None,
) -> dict:
    """
    Procesa mensaje de despacho del viverista
    
    Llamado desde: endpoint despacho_trigger_endpoint
    
    Args:
        numero_viverista: Número WhatsApp del viverista (ej: +573178543819)
        cotizacion_id: ID de la cotización/transacción
        vivero_id: ID del vivero
        mensaje_texto: Texto opcional del mensaje
        media_id: ID del media (foto) en Meta
    
    Returns:
        {
            "ok": bool,
            "entrega_id": int (si éxito),
            "error": str (si error),
            "payout_60_creado": bool
        }
    """
    
    db = admin()
    
    try:
        logger.info(
            f"[DESPACHO] Procesando: vivero={vivero_id}, "
            f"cotizacion={cotizacion_id}, media={media_id}"
        )
        
        # 1. VERIFICAR QUE EXISTA ENTREGAS PARA ESTA COTIZACIÓN
        entregas_result = db.table("entregas").select("entrega_id").eq(
            "cotizacion_id", cotizacion_id
        ).execute()
        
        if not entregas_result.data:
            logger.warning(f"[DESPACHO] No existe entrega para cotizacion {cotizacion_id}")
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
                logger.info(f"[DESPACHO] Descargando media {media_id}...")
                foto_bytes = await download_media_bytes(media_id)
                
                # TODO: Subir a storage (ej: Supabase Storage, AWS S3)
                # Por ahora almacenar URL en BD
                # Formato: "media:{media_id}" para identificar después
                foto_url = f"media:{media_id}"
                
                logger.info(f"[DESPACHO] Foto descargada: {len(foto_bytes)} bytes")
                
            except Exception as e:
                logger.error(f"[DESPACHO] Error descargando media: {str(e)}")
                # No es bloqueante, continuar sin foto
        
        # 3. ACTUALIZAR ENTREGA A "DESPACHADO"
        try:
            db.table("entregas").update({
                "estado_entrega": "despachado",
                "fecha_despacho": datetime.utcnow().isoformat(),
                "foto_despacho": foto_url,
                "timestamp_despacho": datetime.utcnow().isoformat(),
                "notas_vivero": mensaje_texto or "Despacho confirmado por WhatsApp"
            }).eq("entrega_id", entrega_id).execute()
            
            logger.info(f"[DESPACHO] Entrega {entrega_id} marcada como despachada")
            
        except Exception as e:
            logger.error(f"[DESPACHO] Error actualizando entrega: {str(e)}")
            return {
                "ok": False,
                "error": "update_failed",
                "mensaje": f"Error actualizando entrega: {str(e)}"
            }
        
        # 4. OBTENER DATOS DE PAGO PARA CREAR TRANSFERENCIA
        try:
            cotizacion_result = db.table("cotizaciones").select("*").eq(
                "cotizacion_id", cotizacion_id
            ).single().execute()
            
            if not cotizacion_result.data:
                logger.error(f"[DESPACHO] Cotización {cotizacion_id} no encontrada")
                return {
                    "ok": False,
                    "error": "cotizacion_not_found",
                    "mensaje": f"Cotización {cotizacion_id} no encontrada"
                }
            
            cotizacion = cotizacion_result.data
            # Obtener pago_id desde transaccion_id
            monto_total = float(cotizacion.get("monto_total", 0))
            
        except Exception as e:
            logger.error(f"[DESPACHO] Error obteniendo cotización: {str(e)}")
            return {
                "ok": False,
                "error": "cotizacion_error",
                "mensaje": f"Error obteniendo cotización: {str(e)}"
            }
        
        # 5. BUSCAR pago_id ASOCIADO
        try:
            pagos_result = db.table("pagos").select("pago_id").eq(
                "estado_pago", "aprobado"
            ).eq(
                "monto_total", monto_total
            ).limit(1).execute()
            
            if not pagos_result.data:
                logger.warning(f"[DESPACHO] No se encontró pago aprobado para monto {monto_total}")
                # No es error bloqueante, continuar
                pago_id = None
            else:
                pago_id = pagos_result.data[0]["pago_id"]
            
        except Exception as e:
            logger.error(f"[DESPACHO] Error buscando pago: {str(e)}")
            pago_id = None
        
        # 6. CREAR TRANSFERENCIA VIVERISTA - PAYOUT 60%
        payout_60_creado = False
        if pago_id:
            try:
                # Calcular split Escenario 3: 3% VO, 97% viverista, 60% despacho
                comision_vo_60 = (monto_total * 0.03) * 0.60
                monto_viverista_60 = (monto_total * 0.97) * 0.60
                
                transfer_result = db.table("transferencias_viverista").insert({
                    "pago_id": pago_id,
                    "vivero_id": vivero_id,
                    "monto_plantas": monto_total,
                    "monto_flete": 0,
                    "viverista_plantas": monto_viverista_60,
                    "viverista_flete": 0,
                    "viverista_total": monto_viverista_60,
                    "plataforma_plantas": comision_vo_60,
                    "plataforma_flete": 0,
                    "plataforma_total": comision_vo_60,
                    "estado": "enviado",
                    "referencia_banco": f"TR_{pago_id}_DESP_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                    "fecha_transferencia": datetime.utcnow().isoformat(),
                }).execute()
                
                logger.info(
                    f"[DESPACHO] Payout 60% creado: pago={pago_id}, "
                    f"viverista=${monto_viverista_60:.2f}"
                )
                payout_60_creado = True
                
                # TODO: Notificar viverista por WhatsApp
                logger.info(f"[DESPACHO] TODO: Notificar viverista sobre Payout 60%")
                
            except Exception as e:
                logger.error(f"[DESPACHO] Error creando transferencia: {str(e)}")
                # No es bloqueante
        
        # 7. REGISTRAR EVENTO EN ticket_eventos_whatsapp
        try:
            db.table("ticket_eventos_whatsapp").insert({
                "ticket_id": entrega_id,  # Usar entrega_id como referencia
                "whatsapp_numero": numero_viverista,
                "tipo_evento": "despacho_confirmado",
                "estado": "procesado",
                "json_response": f"Despacho confirmado por viverista {numero_viverista}",
                "fecha_creacion": datetime.utcnow().isoformat()
            }).execute()
            
            logger.info(f"[DESPACHO] Evento registrado en BD")
            
        except Exception as e:
            logger.error(f"[DESPACHO] Error registrando evento: {str(e)}")
            # No es bloqueante
        
        # 8. RESPUESTA EXITOSA
        return {
            "ok": True,
            "entrega_id": entrega_id,
            "payout_60_creado": payout_60_creado,
            "foto_url": foto_url,
            "mensaje": f"Despacho registrado exitosamente"
        }
        
    except Exception as e:
        logger.error(f"[DESPACHO] Error general: {str(e)}", exc_info=True)
        return {
            "ok": False,
            "error": "general_error",
            "mensaje": str(e)
        }
