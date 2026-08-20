"""Webhook y rutas para Bot WhatsApp.

Gestiona:
- POST /api/whatsapp/webhook (recibe mensajes)
- GET /api/whatsapp/webhook (verifica webhook)
- Consultas de precio (diferenciadas: cliente vs viverista)
- Creación de tickets
- Notificación a admin
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone

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

# Plantas conocidas para detectar consultas de precio
PLANTAS_CONOCIDAS = [
    "hiedra", "geranio", "duranta", "afelandra", "palma", "ficus",
    "begonia", "helecho", "dracena", "calathea", "monstera", "peperomia",
    "clusia", "cordyline", "cheflera", "aglaonema", "espatifilo",
    "lengua", "bambú", "buganvilla", "coleo", "primavera", "pensamiento",
    "kokedama", "sustrato", "ixora", "heliconia", "cycas", "strelitzia",
    "schefflera", "sansevieria", "ficus lyrata"
]


@router.post("/api/whatsapp/webhook")
async def webhook_whatsapp(request: Request):
    """Recibe mensajes de Meta WhatsApp Cloud API."""
    try:
        body_bytes = await request.body()
        signature_header = request.headers.get("x-hub-signature-256", "")
        
        if not verify_signature(body_bytes, signature_header):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        data = json.loads(body_bytes)
        
        if data.get("entry"):
            for entry in data["entry"]:
                for change in entry.get("changes", []):
                    messages = change.get("value", {}).get("messages", [])
                    if messages:
                        await _procesar_mensaje(change["value"])
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.exception(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _procesar_mensaje(webhook_data: dict):
    """Procesa un mensaje recibido."""
    try:
        # Extraer datos
        whatsapp_num = webhook_data.get("contacts", [{}])[0].get("wa_id", "")
        nombre = webhook_data.get("contacts", [{}])[0].get("profile", {}).get("name", "Cliente")
        messages = webhook_data.get("messages", [])
        
        if not whatsapp_num or not messages:
            return
        
        mensaje_texto = messages[0].get("text", {}).get("body", "").strip()
        if not mensaje_texto:
            return
        
        logger.info(f"📱 Mensaje de {whatsapp_num}: {mensaje_texto[:50]}")
        
        lower = mensaje_texto.lower()
        
        # ══════════════════════════════════════════════════════════════════
        # ✅ 1. DETECTOR DE PRECIO + PLANTA (RESPUESTA DIFERENCIADA)
        # ══════════════════════════════════════════════════════════════════
        if "precio" in lower:
            planta_mencionada = None
            for planta in PLANTAS_CONOCIDAS:
                if planta in lower:
                    planta_mencionada = planta
                    break
            
            if planta_mencionada:
                logger.info(f"🔍 Detecto: 'precio' + '{planta_mencionada}'")
                try:
                    # Verificar si es viverista registrado
                    es_viverista = False
                    try:
                        vivero_resp = admin().table("viveros").select(
                            "vivero_id"
                        ).eq("mandato_whatsapp_numero", whatsapp_num).limit(1).execute()
                        es_viverista = bool(vivero_resp.data)
                        logger.info(f"Tipo usuario: {'viverista' if es_viverista else 'cliente'}")
                    except Exception as e:
                        logger.warning(f"Error verificando viverista: {e}")
                    
                    # ✅ RESPUESTA DIFERENCIADA
                    if es_viverista:
                        # ─ VIVERISTA REGISTRADO: RESPUESTA LARGA
                        logger.info(f"📊 Respondiendo a VIVERISTA")
                        
                        plantas_resp = admin().table("plantas").select(
                            "planta_id, nombre_comun"
                        ).ilike("nombre_comun", f"%{planta_mencionada}%").limit(1).execute()
                        
                        if plantas_resp.data:
                            planta = plantas_resp.data[0]
                            planta_id = planta.get("planta_id")
                            nombre_comun = planta.get("nombre_comun")
                            
                            inventario_resp = admin().table("inventario").select(
                                "stock, precio_mayorista, inventario_id"
                            ).eq("planta_id", planta_id).limit(1).execute()
                            
                            if inventario_resp.data:
                                inventario = inventario_resp.data[0]
                                stock = inventario.get("stock", 0)
                                precio_mayorista = inventario.get("precio_mayorista", 0)
                                
                                precio_mayorista_fmt = f"${int(precio_mayorista):,}".replace(",", ".")
                                precio_cliente = int(precio_mayorista * 1.20)
                                precio_cliente_fmt = f"${precio_cliente:,}".replace(",", ".")
                                
                                respuesta = (
                                    f"¡Claro! Con gusto te doy la información de la {nombre_comun} que tienes en tu inventario:\n\n"
                                    f"*{nombre_comun}*\n"
                                    f"• *Stock disponible:* {stock} unidades\n"
                                    f"• *Tu precio (lo que recibirás):* {precio_mayorista_fmt} COP\n"
                                    f"• *Precio al comprador (con 20% orquestación):* {precio_cliente_fmt} COP\n\n"
                                    f"Esta planta es excelente para cubrir muros, jardineras o como planta colgante.\n\n"
                                    f"¿Necesitas información sobre alguna otra planta o quieres que revisemos algo más de tu inventario?"
                                )
                                
                                await send_text_message(whatsapp_num, respuesta)
                                return
                    
                    # ─ CLIENTE (NO viverista): RESPUESTA CORTA
                    respuesta = await procesar_consulta_precio_producto(
                        supabase=admin(),
                        producto_nombre=planta_mencionada,
                        es_guest=True,
                        plazo="inmediato",
                    )
                    logger.info(f"✅ Precio consultado")
                    await send_text_message(whatsapp_num, respuesta)
                    return
                    
                except Exception as e:
                    logger.error(f"❌ Error consultando precio: {e}")
                    await send_text_message(whatsapp_num, "⚠️ Error al consultar precio. Intentá de nuevo.")
                    return
        
        # ══════════════════════════════════════════════════════════════════
        # 2. MENÚ INICIAL
        # ══════════════════════════════════════════════════════════════════
        if lower in ("hola", "hi", "start", "inicio"):
            await send_text_message(
                whatsapp_num,
                "🌱 ¡Hola! Bienvenido a ViveroOnline.com.co\n\n¿Qué necesitás hoy?\n\n1️⃣ Comprar plantas vivas\n2️⃣ Vender mis plantas (soy viverista)\n3️⃣ Solo consultar"
            )
            return
        
        # ══════════════════════════════════════════════════════════════════
        # 3. OPCIONES DEL MENÚ
        # ══════════════════════════════════════════════════════════════════
        if mensaje_texto in ("1", "1️⃣"):
            await send_text_message(
                whatsapp_num,
                "📍 Explora nuestro catálogo de plantas vivas:\nhttps://app.viveroonline.com.co/marketplace"
            )
            return
        
        if mensaje_texto in ("2", "2️⃣"):
            await send_text_message(
                whatsapp_num,
                "🌳 Registrate como viverista y vende sin intermediarios:\nhttps://app.viveroonline.com.co/registro-vivero"
            )
            return
        
        if mensaje_texto in ("3", "3️⃣"):
            await send_text_message(
                whatsapp_num,
                "💬 ¡Con gusto te ayudo!\n\nPodés ver precios y catálogo acá:\nhttps://viveroonline.com.co\n\nO si preferís, contame qué necesitás (nombre + consulta) y te contactamos.\n\nEjemplo: 'Soy Ana, quiero saber precio de 200 arizónicas para conjunto en Chía'"
            )
            return
        
        if lower in ("salir", "exit", "fin"):
            await send_text_message(whatsapp_num, "Sesión cerrada. ¡Hasta pronto! 🌿")
            return
        
        # ══════════════════════════════════════════════════════════════════
        # 4. CREAR TICKET Y NOTIFICAR ADMIN
        # ══════════════════════════════════════════════════════════════════
        logger.info(f"📝 Creando ticket para {whatsapp_num}")
        
        try:
            ticket_data = {
                "whatsapp_numero": whatsapp_num,
                "nombre": nombre,
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
            
            # Notificar admin
            if ADMIN_WHATSAPP:
                try:
                    msg_admin = (
                        f"🔵 *Ticket #{ticket_id}* — CONSULTA\n\n"
                        f"👤 Cliente: {nombre}\n"
                        f"📱 WhatsApp: {whatsapp_num}\n\n"
                        f"💬 Solicitud:\n{mensaje_texto[:200]}\n\n"
                        f"🔗 Panel: https://app.viveroonline.com.co/admin"
                    )
                    await send_text_message(ADMIN_WHATSAPP, msg_admin)
                    logger.info(f"✅ Admin notificado de ticket #{ticket_id}")
                except Exception as e:
                    logger.error(f"❌ Error notificando admin: {e}")
            
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
