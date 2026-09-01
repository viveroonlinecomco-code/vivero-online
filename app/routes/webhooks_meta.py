"""
Webhooks de Meta para rastreo de entregas WhatsApp.
Endpoints para recibir confirmaciones de:
- delivered: mensaje entregado
- read: mensaje leído
- failed: envío fallido
"""

import logging
import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from app.services.supabase import admin
from app.services.whatsapp_meta import verify_signature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/whatsapp", tags=["webhooks"])


@router.get("/status")
async def webhook_verify(request: Request):
    """Verificación de webhook Meta - devuelve challenge como texto plano"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    verify_token = os.getenv("WEBHOOK_VERIFY_TOKEN", "vivero_webhook_secure_token")
    logger.info(f"🔍 GET /status: mode={mode}, token_ok={token==verify_token}")
    if mode == "subscribe" and token == verify_token:
        logger.info("✅ Webhook verificado")
        return PlainTextResponse(challenge)
    logger.warning(f"❌ Verificación fallida")
    return PlainTextResponse("", status_code=403)


@router.post("/status")
async def webhook_status(request: Request):
    """
    Recibe notificaciones de Meta sobre estado de mensajes.
    Estados: delivered, read, failed
    """
    try:
        # Obtener firma y cuerpo
        body_bytes = await request.body()
        signature = request.headers.get("x-hub-signature-256", "")
        
        # Verificar firma en producción
        if os.getenv("ENV") == "production":
            if not verify_signature(body_bytes, signature):
                logger.warning("❌ Firma de webhook inválida")
                return JSONResponse(status_code=403, content={"ok": False})
        
        # Parsear payload
        payload = await request.json()
        logger.info(f"📨 Webhook recibido: {len(payload.get('entry', []))} entries")
        
    except Exception as e:
        logger.error(f"❌ Error parseando webhook: {e}")
        return {"ok": True}  # Responder OK igual para no hacer reintentar a Meta
    
    # Procesar cambios de estado
    try:
        db = admin()
        
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue
                
                value = change.get("value", {})
                statuses = value.get("statuses", [])
                
                # Procesar cada status
                for status in statuses:
                    try:
                        message_id = status.get("id")
                        status_value = status.get("status")  # delivered, read, failed
                        recipient = status.get("recipient_id")
                        timestamp = status.get("timestamp")
                        
                        if not message_id:
                            continue
                        
                        logger.info(
                            f"📍 Meta notifica: {message_id[:20]}... → {status_value} "
                            f"(destinatario: +{recipient})"
                        )
                        
                        # Intentar actualizar evento existente
                        try:
                            response = db.table("ticket_eventos_whatsapp").select(
                                "evento_id"
                            ).eq("meta_message_id", message_id).limit(1).execute()
                            
                            if response.data:
                                # Actualizar estado
                                db.table("ticket_eventos_whatsapp").update({
                                    "estado": status_value,
                                    "json_response": str(status)
                                }).eq("meta_message_id", message_id).execute()
                                
                                logger.info(
                                    f"✅ Evento actualizado: {message_id[:20]}... → {status_value}"
                                )
                            else:
                                # Crear nuevo evento si no existe
                                db.table("ticket_eventos_whatsapp").insert({
                                    "whatsapp_numero": f"+{recipient}" if recipient else None,
                                    "meta_message_id": message_id,
                                    "tipo_evento": "status_update",
                                    "estado": status_value,
                                    "json_response": str(status)
                                }).execute()
                                
                                logger.info(
                                    f"📝 Evento creado: {message_id[:20]}... → {status_value}"
                                )
                                
                        except Exception as db_err:
                            logger.error(
                                f"❌ Error guardando evento {message_id}: {db_err}"
                            )
                            # Continuar con siguientes mensajes
                    
                    except Exception as status_err:
                        logger.error(f"❌ Error procesando status individual: {status_err}")
                        # Continuar sin romper el flujo
    
    except Exception as e:
        logger.exception(f"❌ Error general procesando webhook: {e}")
    
    # SIEMPRE responder 200 OK a Meta para que no reintente
    return {"ok": True}
