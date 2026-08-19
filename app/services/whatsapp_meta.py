"""Bot WhatsApp Simple y Robusto — SIN dependencias complejas."""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from app.services.supabase import admin
from app.services.whatsapp_meta import (
    send_text_message,
    verify_signature,
    procesar_consulta_precio_producto,
)

logger = logging.getLogger(__name__)
router = APIRouter()

VERIFY_TOKEN = os.getenv("META_WA_VERIFY_TOKEN", "")
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP_NOTIF", "")


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/whatsapp/webhook")
async def webhook_whatsapp(request: Request):
    """Recibe mensajes de Meta WhatsApp."""
    body_bytes = await request.body()
    signature_header = request.headers.get("x-hub-signature-256", "")
    
    if not verify_signature(body_bytes, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    if data.get("entry"):
        for entry in data["entry"]:
            if entry.get("changes"):
                for change in entry["changes"]:
                    if change.get("value", {}).get("messages"):
                        await _procesar_mensaje(change["value"])
    
    return {"status": "ok"}


async def _procesar_mensaje(webhook_data: dict):
    """Procesa un mensaje recibido."""
    try:
        whatsapp_num = webhook_data.get("contacts", [{}])[0].get("wa_id", "")
        nombre_cliente = webhook_data.get("contacts", [{}])[0].get("profile", {}).get("name", "Cliente")
        
        if not whatsapp_num:
            return
        
        messages = webhook_data.get("messages", [])
        if not messages:
            return
        
        mensaje_texto = messages[0].get("text", {}).get("body", "").strip()
        if not mensaje_texto:
            return
        
        logger.info(f"📱 Mensaje de {whatsapp_num}: {mensaje_texto[:50]}")
        
        lower = mensaje_texto.lower()
        
        # ═══════════════════════════════════════════════════════════════════
        # 1️⃣ HANDLER PRECIO — Prioritario
        # ═══════════════════════════════════════════════════════════════════
        if lower.startswith("precio "):
            producto_nombre = lower.replace("precio ", "", 1).strip()
            if producto_nombre:
                try:
                    logger.info(f"🔍 Consultando precio: {producto_nombre}")
                    respuesta = await procesar_consulta_precio_producto(
                        supabase=admin(),
                        producto_nombre=producto_nombre,
                        es_guest=False,
                        plazo="inmediato",
                    )
                    logger.info(f"✅ Respuesta precio: {respuesta[:50]}")
                    await send_text_message(whatsapp_num, respuesta)
                    return
                except Exception as e:
                    logger.error(f"❌ Error precio: {e}")
                    await send_text_message(whatsapp_num, "⚠️ Error al consultar precio. Intentá de nuevo.")
                    return
        
        # ═══════════════════════════════════════════════════════════════════
        # 2️⃣ SALUDO INICIAL — Primera vez
        # ═══════════════════════════════════════════════════════════════════
        if lower in ("hola", "hi", "buenos días", "buenas tardes", "buenas noches", "buenas"):
            await send_text_message(
                whatsapp_num,
                "🌱 ¡Hola! Bienvenido a ViveroOnline.com.co\n\n¿Qué necesitás hoy?\n1️⃣ Comprar plantas\n2️⃣ Vender mis plantas\n3️⃣ Consultar"
            )
            return
        
        # ═══════════════════════════════════════════════════════════════════
        # 3️⃣ COMANDOS DEL MENÚ
        # ═══════════════════════════════════════════════════════════════════
        if mensaje_texto in ("1", "1️⃣"):
            await send_text_message(whatsapp_num, "📍 Explora nuestro catálogo:\nhttps://app.viveroonline.com.co/marketplace")
            return
        
        if mensaje_texto in ("2", "2️⃣"):
            await send_text_message(whatsapp_num, "🌳 ¡Queremos contar con vos! Registrate como viverista:\nhttps://app.viveroonline.com.co/registro-vivero")
            return
        
        if mensaje_texto in ("3", "3️⃣"):
            await send_text_message(whatsapp_num, "❓ Perfecto, escribe tu consulta y te respondemos pronto.")
            return
        
        if lower in ("salir", "exit", "fin"):
            await send_text_message(whatsapp_num, "Sesión cerrada. ¡Hasta pronto! 🌿")
            return
        
        # ═══════════════════════════════════════════════════════════════════
        # 4️⃣ TICKET POR DEFECTO + NOTIFICACIÓN ADMIN
        # ═══════════════════════════════════════════════════════════════════
        logger.info(f"📝 Creando ticket para {whatsapp_num}")
        
        try:
            # Crear ticket
            ticket_data = {
                "whatsapp_numero": whatsapp_num,
                "nombre": nombre_cliente,
                "tipo_solicitud": "consulta",
                "descripcion": mensaje_texto,
                "estado": "abierto",
                "fecha_creacion": datetime.now(timezone.utc).isoformat(),
            }
            
            result = admin().table("tickets").insert(ticket_data).execute()
            
            if not result.data:
                logger.error("No se pudo crear ticket")
                await send_text_message(whatsapp_num, "✅ Tu solicitud fue recibida. Te contactaremos pronto.")
                return
            
            ticket_id = result.data[0].get("id")
            logger.info(f"✅ Ticket #{ticket_id} creado")
            
            # ✅ NOTIFICAR AL ADMIN INMEDIATAMENTE
            if ADMIN_WHATSAPP:
                try:
                    msg_admin = (
                        f"🔵 *Ticket #{ticket_id}* — CONSULTA\n\n"
                        f"👤 Cliente: {nombre_cliente}\n"
                        f"📱 WhatsApp: {whatsapp_num}\n\n"
                        f"💬 Solicitud:\n{mensaje_texto[:200]}\n\n"
                        f"🔗 Panel: https://app.viveroonline.com.co/admin"
                    )
                    
                    await send_text_message(ADMIN_WHATSAPP, msg_admin)
                    logger.info(f"✅ Admin notificado de ticket #{ticket_id}")
                except Exception as e:
                    logger.error(f"❌ Error notificando admin: {e}")
            else:
                logger.warning("⚠️ ADMIN_WHATSAPP_NOTIF no configurado")
            
            # Responder al usuario
            await send_text_message(
                whatsapp_num,
                f"✅ ¡Recibí tu solicitud! (ticket #{ticket_id})\n\nNuestro equipo te contactará en las próximas horas. 🌿"
            )
            
        except Exception as e:
            logger.error(f"❌ Error creando ticket: {e}")
            await send_text_message(whatsapp_num, "✅ Tu solicitud fue recibida.")
        
    except Exception as e:
        logger.exception(f"❌ Error procesando mensaje: {e}")


@router.get("/api/whatsapp/webhook")
async def verify_whatsapp_webhook(
    hub_mode: str = "",
    hub_challenge: str = "",
    hub_verify_token: str = "",
):
    """Verifica webhook con Meta."""
    if hub_verify_token == VERIFY_TOKEN:
        logger.info("✅ WhatsApp webhook verificado")
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    
    logger.warning("❌ Token de verificación inválido")
    raise HTTPException(status_code=403, detail="Invalid token")
