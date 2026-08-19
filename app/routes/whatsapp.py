"""Webhook y rutas para Bot WhatsApp integrado con LangGraph + tienda marketplace.

Gestiona:
- Webhook POST /api/whatsapp/webhook (recibe mensajes)
- Sesiones por usuario (chat context)
- Flujo LangGraph para compradores/viveristas
- Tickets de soporte
- Consultas de precio (BI/B2C)
- Onboarding viveristas
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.services.supabase import admin
from app.services.whatsapp_meta import (
    send_text_message,
    verify_signature,
    procesar_consulta_precio_producto,
    obtener_recomendacion_producto,
)
from app.routes.ticket_responder import (
    responder_ticket_segun_tipo,
    notificar_admin_con_contexto,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════════════════

VERIFY_TOKEN = os.getenv("META_WA_VERIFY_TOKEN", "")
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP_NOTIF", "")


class WebhookMessage(BaseModel):
    messaging_product: str
    entry: list


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _obtener_sesion_cliente(whatsapp: str) -> Optional[dict]:
    """Obtiene o crea sesión del cliente en BD."""
    try:
        resp = (
            admin()
            .table("whatsapp_sesiones")
            .select("*")
            .eq("whatsapp_numero", whatsapp)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]
        return None
    except Exception as e:
        logger.warning(f"Error obteniendo sesión {whatsapp}: {e}")
        return None


def _crear_sesion_cliente(whatsapp: str, rol: str = "comprador") -> dict:
    """Crea nueva sesión para cliente."""
    try:
        sesion = {
            "whatsapp_numero": whatsapp,
            "rol": rol,
            "estado": "activa",
            "mensaje_inicial": False,
            "fecha_creacion": datetime.now(timezone.utc).isoformat(),
        }
        result = admin().table("whatsapp_sesiones").insert(sesion).execute()
        if result.data:
            return result.data[0]
        return sesion
    except Exception as e:
        logger.warning(f"Error creando sesión {whatsapp}: {e}")
        return {"whatsapp_numero": whatsapp, "rol": rol, "error": str(e)}


def _detectar_rol(whatsapp: str, mensaje: str) -> str:
    """Detecta rol del usuario (viverista/comprador/admin/guest)."""
    lower = mensaje.lower()
    
    # Viverista
    if any(kw in lower for kw in ["vivero", "vendo", "soy viverista", "productos", "precio mayorista"]):
        return "viverista"
    
    # Buscar en BD si está registrado
    try:
        cliente_resp = (
            admin()
            .table("clientes")
            .select("cliente_id")
            .eq("whatsapp_principal", whatsapp)
            .limit(1)
            .execute()
        )
        if cliente_resp.data:
            return "comprador"
        
        vivero_resp = (
            admin()
            .table("viveros")
            .select("vivero_id")
            .eq("mandato_whatsapp_numero", whatsapp)
            .limit(1)
            .execute()
        )
        if vivero_resp.data:
            return "viverista"
    except Exception:
        pass
    
    # Por defecto
    return "guest"


def _save_message(sesion_id: str, role: str, content: str, agente: str = "user"):
    """Guarda mensaje en historial."""
    try:
        admin().table("whatsapp_mensajes").insert({
            "sesion_id": sesion_id,
            "rol": role,
            "contenido": content,
            "agente": agente,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.warning(f"Error guardando mensaje: {e}")


def _close_session(sesion_id: str):
    """Cierra sesión."""
    try:
        admin().table("whatsapp_sesiones").update({
            "estado": "cerrada",
            "fecha_cierre": datetime.now(timezone.utc).isoformat(),
        }).eq("sesion_id", sesion_id).execute()
    except Exception as e:
        logger.warning(f"Error cerrando sesión {sesion_id}: {e}")


async def _crear_ticket_y_notificar(
    whatsapp: str,
    nombre: str,
    tipo: str,
    descripcion: str,
    sesion_id: Optional[str] = None,
):
    """Crea ticket y notifica al admin."""
    try:
        # Crear ticket
        ticket_data = {
            "whatsapp_numero": whatsapp,
            "nombre": nombre,
            "tipo_solicitud": tipo,
            "descripcion": descripcion,
            "estado": "abierto",
            "fecha_creacion": datetime.now(timezone.utc).isoformat(),
            "sesion_id": sesion_id,
        }
        
        result = admin().table("tickets").insert(ticket_data).execute()
        if not result.data:
            logger.error(f"No se pudo crear ticket para {whatsapp}")
            return
        
        ticket_id = result.data[0].get("id")
        
        # Auto-responder según tipo
        respuesta_info = await responder_ticket_segun_tipo(ticket_id, ticket_data)
        
        # Notificar admin
        if ADMIN_WHATSAPP:
            await notificar_admin_con_contexto(
                ticket_id=ticket_id,
                ticket_data=ticket_data,
                respuesta_info=respuesta_info,
                admin_whatsapp=ADMIN_WHATSAPP,
            )
        
        logger.info(f"Ticket #{ticket_id} creado para {whatsapp}")
        
    except Exception as e:
        logger.error(f"Error creando ticket: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/whatsapp/webhook")
async def webhook_whatsapp(request: Request):
    """Recibe mensajes de Meta WhatsApp Cloud API."""
    
    # Validar firma
    body_bytes = await request.body()
    signature_header = request.headers.get("x-hub-signature-256", "")
    
    if not verify_signature(body_bytes, signature_header):
        logger.warning("Firma WhatsApp inválida")
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    # Procesar entrada
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
            logger.warning("No se pudo extraer número WhatsApp")
            return
        
        messages = webhook_data.get("messages", [])
        if not messages:
            return
        
        mensaje_data = messages[0]
        mensaje_texto = mensaje_data.get("text", {}).get("body", "").strip()
        
        if not mensaje_texto:
            logger.debug(f"Mensaje vacío de {whatsapp_num}")
            return
        
        logger.info(f"Mensaje de {whatsapp_num}: {mensaje_texto[:50]}")
        
        # Obtener/crear sesión
        sesion = _obtener_sesion_cliente(whatsapp_num)
        if not sesion:
            sesion = _crear_sesion_cliente(whatsapp_num)
        
        sesion_id = sesion.get("sesion_id") or sesion.get("id")
        rol = sesion.get("rol", _detectar_rol(whatsapp_num, mensaje_texto))
        lower = mensaje_texto.lower()
        
        # Saludo inicial
        if not sesion.get("mensaje_inicial"):
            await send_text_message(
                whatsapp_num,
                "🌱 ¡Hola! Bienvenido a ViveroOnline.com.co\n\n¿Qué necesitás hoy?\n1️⃣ Comprar plantas\n2️⃣ Vender mis plantas\n3️⃣ Consultar"
            )
            admin().table("whatsapp_sesiones").update({
                "mensaje_inicial": True
            }).eq("sesion_id", sesion_id).execute()
            return
        
        # ══════════════════════════════════════════════════════════════════
        # ✅ FIX 19 AGO - CONSULTA DE PRECIO PARA COMPRADOR REGISTRADO
        # ══════════════════════════════════════════════════════════════════
        if rol in ("comprador", "admin") and lower.startswith("precio "):
            producto_nombre = lower.replace("precio ", "", 1).strip()
            if producto_nombre:
                try:
                    respuesta = await procesar_consulta_precio_producto(
                        supabase=admin(),
                        producto_nombre=producto_nombre,
                        es_guest=False,
                        plazo="inmediato",
                    )
                    _save_message(sesion_id, "assistant", respuesta, agente="precio_bot")
                    await send_text_message(whatsapp_num, respuesta)
                    return
                except Exception as e:
                    logger.error(f"Error procesando consulta de precio: {e}")
                    await send_text_message(whatsapp_num, "⚠️ Error al consultar precio. Intentá de nuevo.")
                    return
        
        # Comandos de control
        if lower in ("salir", "exit", "fin"):
            _close_session(sesion_id)
            await send_text_message(whatsapp_num, "Sesión cerrada. ¡Hasta pronto! 🌿")
            return
        
        # Opciones del menú
        if mensaje_texto in ("1", "1️⃣"):
            respuesta = "📍 Explora el catálogo:\nhttps://app.viveroonline.com.co/marketplace"
            await send_text_message(whatsapp_num, respuesta)
            return
        
        if mensaje_texto in ("2", "2️⃣"):
            respuesta = "🌳 Registrate como viverista:\nhttps://app.viveroonline.com.co/registro-vivero"
            await send_text_message(whatsapp_num, respuesta)
            return
        
        if mensaje_texto in ("3", "3️⃣"):
            respuesta = "❓ Escribe tu consulta o escribí 'ayuda' para opciones"
            await send_text_message(whatsapp_num, respuesta)
            return
        
        # Crear ticket por defecto
        await _crear_ticket_y_notificar(
            whatsapp=whatsapp_num,
            nombre=nombre_cliente,
            tipo="consulta",
            descripcion=mensaje_texto,
            sesion_id=sesion_id,
        )
        
        await send_text_message(
            whatsapp_num,
            f"✅ ¡Recibí tu solicitud! (ticket #{sesion_id[:6]})\n\nNuestro equipo te contactará por WhatsApp en las próximas horas."
        )
        
    except Exception as e:
        logger.exception(f"Error procesando mensaje: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK VERIFICACIÓN (GET)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/whatsapp/webhook")
async def verify_whatsapp_webhook(
    hub_mode: str = "",
    hub_challenge: str = "",
    hub_verify_token: str = "",
):
    """Verifica el webhook con Meta."""
    if hub_verify_token == VERIFY_TOKEN:
        logger.info("WhatsApp webhook verificado")
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    
    logger.warning("Token de verificación inválido")
    raise HTTPException(status_code=403, detail="Invalid verification token")
