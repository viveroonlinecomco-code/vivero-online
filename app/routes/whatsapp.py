"""Webhook WhatsApp — Versión robusta.

Garantiza: crear ticket → responder usuario → notificar admin
Sin fallos silenciosos.
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
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP_NOTIF", "").strip()

logger.info(f"🔧 ADMIN_WHATSAPP_NOTIF configurado: {bool(ADMIN_WHATSAPP)}")
if ADMIN_WHATSAPP:
    logger.info(f"   Número: {ADMIN_WHATSAPP}")

PLANTAS_CONOCIDAS = [
    "hiedra", "geranio", "duranta", "afelandra", "palma", "ficus",
    "begonia", "helecho", "dracena", "calathea", "monstera", "peperomia",
    "clusia", "cordyline", "cheflera", "aglaonema", "espatifilo",
    "lengua", "bambú", "buganvilla", "coleo", "primavera", "pensamiento",
    "kokedama", "sustrato", "ixora", "heliconia", "cycas", "strelitzia",
    "schefflera", "sansevieria", "ficus lyrata", "jasmin", "arizónica", "arbusto"
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
            logger.warning("No hay número o mensajes")
            return
        
        mensaje_texto = messages[0].get("text", {}).get("body", "").strip()
        if not mensaje_texto:
            logger.warning("Mensaje vacío")
            return
        
        logger.info(f"📱 De {whatsapp_num}: {mensaje_texto[:60]}")
        
        lower = mensaje_texto.lower()
        
        # ══════════════════════════════════════════════════════════════════
        # 1. DETECTOR DE PRECIO + PLANTA
        # ══════════════════════════════════════════════════════════════════
        if "precio" in lower:
            planta_mencionada = None
            for planta in PLANTAS_CONOCIDAS:
                if planta in lower:
                    planta_mencionada = planta
                    break
            
            if planta_mencionada:
                logger.info(f"🔍 Consulta de precio: {planta_mencionada}")
                try:
                    es_viverista = False
                    try:
                        vivero_resp = admin().table("viveros").select(
                            "vivero_id"
                        ).eq("mandato_whatsapp_numero", whatsapp_num).limit(1).execute()
                        es_viverista = bool(vivero_resp.data)
                    except:
                        pass
                    
                    if es_viverista:
                        plantas_resp = admin().table("plantas").select(
                            "planta_id, nombre_comun"
                        ).ilike("nombre_comun", f"%{planta_mencionada}%").limit(1).execute()
                        
                        if plantas_resp.data:
                            planta = plantas_resp.data[0]
                            inventario_resp = admin().table("inventario").select(
                                "stock, precio_mayorista"
                            ).eq("planta_id", planta.get("planta_id")).limit(1).execute()
                            
                            if inventario_resp.data:
                                inv = inventario_resp.data[0]
                                stock = inv.get("stock", 0)
                                precio_mayorista = inv.get("precio_mayorista", 0)
                                precio_cliente = int(precio_mayorista * 1.20)
                                
                                respuesta = (
                                    f"¡Claro! Con gusto te doy la información de la {planta.get('nombre_comun')} que tienes en tu inventario:\n\n"
                                    f"*{planta.get('nombre_comun')}*\n"
                                    f"• *Stock disponible:* {stock} unidades\n"
                                    f"• *Tu precio (lo que recibirás):* ${precio_mayorista:,} COP\n"
                                    f"• *Precio al comprador (con 20% orquestación):* ${precio_cliente:,} COP\n\n"
                                    f"¿Necesitas información sobre otra planta?"
                                )
                                await send_text_message(whatsapp_num, respuesta)
                                return
                    
                    respuesta = await procesar_consulta_precio_producto(
                        supabase=admin(),
                        producto_nombre=planta_mencionada,
                        es_guest=True,
                        plazo="inmediato",
                    )
                    await send_text_message(whatsapp_num, respuesta)
                    return
                except Exception as e:
                    logger.error(f"Error precio: {e}")
                    await send_text_message(whatsapp_num, "⚠️ Error al consultar precio.")
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
        # 3. OPCIONES MENÚ
        # ══════════════════════════════════════════════════════════════════
        if mensaje_texto in ("1", "1️⃣"):
            await send_text_message(whatsapp_num, "📍 Explora nuestro catálogo:\nhttps://app.viveroonline.com.co/marketplace")
            return
        
        if mensaje_texto in ("2", "2️⃣"):
            await send_text_message(whatsapp_num, "🌳 Registrate como viverista:\nhttps://app.viveroonline.com.co/registro-vivero")
            return
        
        if mensaje_texto in ("3", "3️⃣"):
            await send_text_message(
                whatsapp_num,
                "💬 ¡Con gusto te ayudo!\n\nPodés ver precios en: https://viveroonline.com.co\n\nO contame qué necesitás (nombre + consulta) y te contactamos.\n\nEjemplo: 'Soy Ana, quiero precio de 200 arizónicas para proyecto en Chía'"
            )
            return
        
        if lower in ("salir", "exit", "fin"):
            await send_text_message(whatsapp_num, "Sesión cerrada. ¡Hasta pronto! 🌿")
            return
        
        # ══════════════════════════════════════════════════════════════════
        # 4. CREAR TICKET (GARANTIZADO)
        # ══════════════════════════════════════════════════════════════════
        logger.info(f"📝 Creando ticket para {nombre} ({whatsapp_num})")
        
        try:
            ticket_data = {
                "whatsapp_numero": whatsapp_num,
                "nombre": nombre,
                "tipo_solicitud": "consulta",
                "descripcion": mensaje_texto,
                "estado": "abierto",
                "fecha_creacion": datetime.now(timezone.utc).isoformat(),
            }
            
            # CREAR TICKET EN BD
            result = admin().table("tickets").insert(ticket_data).execute()
            
            if not result.data:
                logger.error("❌ Insert falló — no data")
                await send_text_message(whatsapp_num, "✅ Tu solicitud fue recibida. Te contactaremos pronto.")
                return
            
            ticket_id = result.data[0].get("id")
            logger.info(f"✅ Ticket #{ticket_id} creado en BD")
            
            # DETERMINAR RESPUESTA AL USUARIO
            respuesta_usuario = _generar_respuesta_coherente(mensaje_texto)
            await send_text_message(whatsapp_num, respuesta_usuario)
            logger.info(f"✅ Respuesta enviada al usuario")
            
            # NOTIFICAR ADMIN
            if ADMIN_WHATSAPP:
                try:
                    msg_admin = (
                        f"🔵 *Ticket #{ticket_id}* — CONSULTA\n\n"
                        f"👤 Cliente: {nombre}\n"
                        f"📱 WhatsApp: {whatsapp_num}\n\n"
                        f"💬 Solicitud:\n{mensaje_texto[:180]}\n\n"
                        f"📨 Respuesta enviada:\n{respuesta_usuario[:200]}\n\n"
                        f"🔗 Panel: https://app.viveroonline.com.co/admin"
                    )
                    result_admin = await send_text_message(ADMIN_WHATSAPP, msg_admin)
                    if result_admin:
                        logger.info(f"✅ Admin notificado de ticket #{ticket_id}")
                    else:
                        logger.error(f"❌ send_text_message devolvió False para admin")
                except Exception as e:
                    logger.error(f"❌ Error notificando admin: {e}")
            else:
                logger.warning(f"⚠️ ADMIN_WHATSAPP_NOTIF NO CONFIGURADO")
        
        except Exception as e:
            logger.exception(f"❌ Error en crear ticket: {e}")
            await send_text_message(whatsapp_num, "✅ Tu solicitud fue recibida.")
    
    except Exception as e:
        logger.exception(f"❌ Error procesando mensaje: {e}")


def _generar_respuesta_coherente(mensaje: str) -> str:
    """Genera respuesta específica según tipo de solicitud."""
    lower = mensaje.lower()
    
    # CONSTRUCTO / PAISAJISMO
    if any(kw in lower for kw in ["constructo", "construcción", "parcelación", "paisajístico", "diseño"]):
        return (
            "🏗️ *Proyecto constructivo - Paisajismo*\n\n"
            "¡Excelente! Tenemos experiencia en proyectos residenciales y comerciales en Sabana de Bogotá.\n\n"
            "✅ Diseño paisajístico\n"
            "✅ Cotización de plantas\n"
            "✅ Entregas a proyecto\n\n"
            "Nuestro equipo te contactará en la próxima hora con propuesta personalizada.\n\n"
            "O escribi a: viveroonline.com.co@gmail.com"
        )
    
    # ENVÍO / DESPACHO / SALITRE
    if any(kw in lower for kw in ["envío", "despacho", "salitre", "entrega", "transporte"]):
        return (
            "🚚 *Información de despacho*\n\n"
            "Hacemos entregas en la Sabana de Bogotá (Chía, Cajicá, Cota, Salitre, Tenjo, Zipaquirá).\n\n"
            "📍 Nuestro equipo te enviará:\n"
            "✅ Opciones de envío\n"
            "✅ Presupuesto de flete\n"
            "✅ Cronograma de entrega\n\n"
            "Te contactaremos en la próxima hora.\n\n"
            "O escribi a: viveroonline.com.co@gmail.com"
        )
    
    # ÁRBOLES / PLANTAS GRANDES
    if any(kw in lower for kw in ["árbol", "año", "grande", "altura", "tamano", "tamaño"]):
        return (
            "🌳 *Árboles y plantas grandes*\n\n"
            "Tenemos árboles de más de 1 año en varias especies.\n\n"
            "📋 Te enviaremos:\n"
            "✅ Catálogo de árboles disponibles\n"
            "✅ Especificaciones (altura, edad)\n"
            "✅ Precios y disponibilidad\n\n"
            "Nuestro equipo te contactará en la próxima hora.\n\n"
            "O escribi a: viveroonline.com.co@gmail.com"
        )
    
    # COMPRA B2B
    if any(kw in lower for kw in ["b2b", "mayorista", "lote", "cantidad", "volumen", "200", "100"]):
        return (
            "🏢 *Cotización B2B - Volumen*\n\n"
            "¡Perfecto! Nos especializamos en compras por volumen.\n\n"
            "📊 Te enviaremos:\n"
            "✅ Disponibilidad de especies\n"
            "✅ Precios mayoristas\n"
            "✅ Opciones de pago\n\n"
            "Nuestro equipo comercial te contactará en la próxima hora.\n\n"
            "O escribi a: viveroonline.com.co@gmail.com"
        )
    
    # GENÉRICA
    return (
        "✅ ¡Recibí tu solicitud!\n\n"
        "Nuestro equipo te contactará en la próxima hora.\n\n"
        "Mientras tanto, explora el catálogo:\n"
        "https://app.viveroonline.com.co/marketplace\n\n"
        "O escribi a: viveroonline.com.co@gmail.com"
    )


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
    
    logger.warning("❌ Token inválido")
    raise HTTPException(status_code=403, detail="Invalid token")
