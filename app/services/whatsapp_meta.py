"""Cliente WhatsApp Meta — Notificaciones, Media, Precios.

ESTRUCTURA DEFINITIVA:
- SIN imports circulares
- TODAS las funciones que whatsapp.py necesita
- Funciones de seguridad (verify_signature)
- Funciones de media (download_media_bytes)
- Funciones de consulta de precios (procesar_consulta_precio_producto)
- Funciones de notificación (notify_viverista_nueva_cotizacion, etc)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

_GRAPH_API = "https://graph.facebook.com/v25.0"


def _phone_id() -> str:
    return os.getenv("META_WA_PHONE_NUMBER_ID", "")


def _access_token() -> str:
    return os.getenv("META_WA_ACCESS_TOKEN", "")


def _verify_token() -> str:
    return os.getenv("WEBHOOK_VERIFY_TOKEN", "vivero_webhook_secure_token")


# ═══════════════════════════════════════════════════════════════
# FUNCIONES DE SEGURIDAD Y VALIDACIÓN
# ═══════════════════════════════════════════════════════════════

def verify_signature(body_bytes: bytes, signature_header: str) -> bool:
    app_secret = os.getenv("META_WA_APP_SECRET", "")
    if not app_secret:
        return True
    
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    
    expected = signature_header[7:]
    computed = hmac.new(
        app_secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()
    
    return hmac.compare_digest(expected, computed)


# ═══════════════════════════════════════════════════════════════
# FUNCIONES DE DESCARGA DE MEDIA
# ═══════════════════════════════════════════════════════════════

async def download_media_bytes(media_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        meta_resp = await client.get(
            f"{_GRAPH_API}/{media_id}",
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json().get("url")
        
        if not media_url:
            raise ValueError("No URL obtenida de Meta")
        
        media_resp = await client.get(
            media_url,
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        media_resp.raise_for_status()
        
        return media_resp.content


# ═══════════════════════════════════════════════════════════════
# FUNCIONES DE CONSULTA DE PRECIOS
# ═══════════════════════════════════════════════════════════════

async def procesar_consulta_precio_producto(
    supabase,
    producto_nombre: str,
    es_guest: bool = True,
    plazo: str = "inmediato",
) -> str:
    try:
        plantas_resp = supabase.table("plantas").select(
            "planta_id, nombre_comun"
        ).ilike("nombre_comun", f"%{producto_nombre}%").limit(1).execute()
        
        if not plantas_resp.data:
            return f"No encontré '{producto_nombre}'. Intenta con: Hiedra, Geranio, Duranta, Afelandra, Palma"
        
        planta = plantas_resp.data[0]
        planta_id = planta.get("planta_id")
        nombre_comun = planta.get("nombre_comun")
        
        inventario_resp = supabase.table("inventario").select(
            "inventario_id, precio_mayorista, stock"
        ).eq("planta_id", planta_id).limit(1).execute()
        
        if not inventario_resp.data:
            return f"'{nombre_comun}' no está disponible en inventario"
        
        inventario = inventario_resp.data[0]
        precio_mayorista = inventario.get("precio_mayorista", 0)
        
        if not precio_mayorista:
            return f"Error: '{nombre_comun}' sin precio"
        
        if es_guest:
            precio_cliente = round(precio_mayorista * 1.18)
        else:
            precio_cliente = round(precio_mayorista * 1.15)
        
        precio_fmt = f"${int(precio_cliente):,.0f}".replace(",", ".")
        
        mensaje = (
            f"Para tu proyecto, la {nombre_comun} "
            f"tiene un precio de {precio_fmt} COP. "
            f"Compra aquí: https://app.viveroonline.com.co/marketplace"
        )
        return mensaje
    
    except Exception as e:
        logger.exception(f"❌ Error en procesar_consulta_precio_producto: {e}")
        return f"⚠️ Error: {str(e)[:100]}"


# ═══════════════════════════════════════════════════════════════
# FUNCIONES PÚBLICAS — ENVÍO DE MENSAJES
# ═══════════════════════════════════════════════════════════════

async def send_text_message(to: str, body: str) -> bool:
    to_clean = to.lstrip("+")
    payload = {
        "messaging_product": "whatsapp",
        "to": to_clean,
        "type": "text",
        "text": {"body": body[:4096]},
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_GRAPH_API}/{_phone_id()}/messages",
                headers={
                    "Authorization": f"Bearer {_access_token()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        return resp.status_code == 200
    except Exception:
        return False


async def send_template_message(
    to: str,
    template_name: str,
    language_code: str = "es_MX",
    parameters: Optional[list[Dict[str, Any]]] = None,
) -> bool:
    to_clean = to.lstrip("+")
    params_body = parameters if parameters else []
    
    payload = {
        "messaging_product": "whatsapp",
        "to": to_clean,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "parameters": {"body": params_body} if params_body else {},
        },
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_GRAPH_API}/{_phone_id()}/messages",
                headers={
                    "Authorization": f"Bearer {_access_token()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        return resp.status_code == 200
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# NOTIFICACIONES ESPECÍFICAS DEL NEGOCIO
# ═══════════════════════════════════════════════════════════════

async def notify_viverista_nueva_cotizacion(
    to: str,
    nombre_viverista: str,
    proyecto: str,
    cliente: str,
    tu_parte_cop: int,
    ciudad_entrega: str,
    horas_para_responder: int = 2,
) -> bool:
    mensaje = (
        f"🌱 ¡Hola {nombre_viverista}!\n\n"
        f"Tienes una nueva cotización en ViveroOnline:\n\n"
        f"📋 Proyecto: {proyecto}\n"
        f"👤 Cliente: {cliente}\n"
        f"💰 Tu parte: ${tu_parte_cop:,.0f} COP\n"
        f"📍 Entrega: {ciudad_entrega}\n"
        f"⏰ Responder en: {horas_para_responder}h\n\n"
        f"👉 Accede a tu dashboard para ver detalles.\n\n"
        f"¡Gracias por ser parte de ViveroOnline! 🚀"
    )
    return await send_text_message(to, mensaje)


async def notify_comprador_cotizacion_enviada(
    to: str,
    nombre_comprador: str,
    proyecto: str,
    total_cop: int,
    num_viveristas: int,
) -> bool:
    mensaje = (
        f"✅ ¡Hola {nombre_comprador}!\n\n"
        f"Tu cotización fue enviada a {num_viveristas} viverista(s):\n\n"
        f"📋 Proyecto: {proyecto}\n"
        f"💰 Monto total: ${total_cop:,.0f} COP\n\n"
        f"Los viveristas responderán pronto con sus propuestas.\n"
        f"Podrás ver todo en tu dashboard.\n\n"
        f"¡Gracias por confiar en ViveroOnline! 🌿"
    )
    return await send_text_message(to, mensaje)


async def notify_comprador_respuesta_recibida(
    to: str,
    nombre_comprador: str,
    proyecto: str,
    nombre_viverista: str,
    monto_cop: int,
) -> bool:
    mensaje = (
        f"📬 ¡{nombre_comprador}! Tienes una respuesta:\n\n"
        f"🌱 {nombre_viverista}\n"
        f"propone: ${monto_cop:,.0f} COP\n\n"
        f"Proyecto: {proyecto}\n\n"
        f"👉 Revisa todas las propuestas en tu dashboard.\n\n"
        f"ViveroOnline 🚀"
    )
    return await send_text_message(to, mensaje)


# ═══════════════════════════════════════════════════════════════
# WEBHOOK VERIFICATION (para Meta)
# ═══════════════════════════════════════════════════════════════

def verify_webhook_token(
    received_token: str,
    expected_token: Optional[str] = None,
) -> bool:
    if expected_token is None:
        expected_token = _verify_token()
    return received_token == expected_token


def generate_webhook_signature(
    payload: str,
    app_secret: Optional[str] = None,
) -> str:
    if app_secret is None:
        app_secret = os.getenv("META_APP_SECRET", "")
    signature = hmac.new(
        app_secret.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature


def verify_webhook_signature(
    payload: str,
    received_signature: str,
    app_secret: Optional[str] = None,
) -> bool:
    generated_sig = generate_webhook_signature(payload, app_secret)
    expected = f"sha256={generated_sig}"
    return hmac.compare_digest(expected, received_signature)


# ═══════════════════════════════════════════════════════════════
# PARSEO DE EVENTOS DE WEBHOOK
# ═══════════════════════════════════════════════════════════════

def parse_webhook_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])
        
        for change in changes:
            messages = change.get("value", {}).get("messages", [])
            if messages:
                return messages[0]
        return None
    except (KeyError, IndexError, TypeError):
        return None


def extract_message_info(message_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        msg_type = message_obj.get("type", "")
        if msg_type != "text":
            return None
        
        info = {
            "from": message_obj.get("from"),
            "type": msg_type,
            "text": message_obj.get("text", {}).get("body", ""),
            "timestamp": message_obj.get("timestamp"),
            "message_id": message_obj.get("id"),
        }
        if info["from"] and info["text"]:
            return info
        return None
    except (KeyError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════
# FUNCIONES DE AUTO-TIMEOUT RESTAURADAS
# ═══════════════════════════════════════════════════════════════

async def notify_viverista_recordatorio(to: str, nombre_viverista: str, proyecto: str) -> bool:
    """Notifica al viverista que el tiempo de respuesta está por expirar."""
    mensaje = (
        f"⏳ ¡Hola {nombre_viverista}!\n\n"
        f"Tienes una solicitud pendiente por responder:\n\n"
        f"📋 Proyecto: *{proyecto}*\n\n"
        f"El tiempo está por agotarse. Por favor, ingresa a tu panel para confirmar o rechazar la disponibilidad.\n\n"
        f"ViveroOnline 🚀"
    )
    return await send_text_message(to, mensaje)


async def notify_viverista_timeout(to: str, nombre_viverista: str, proyecto: str) -> bool:
    """Notifica al viverista que la cotización expiró por falta de respuesta."""
    mensaje = (
        f"❌ ¡Hola {nombre_viverista}!\n\n"
        f"El tiempo para responder a la solicitud ha expirado:\n\n"
        f"📋 Proyecto: *{proyecto}*\n\n"
        f"La solicitud ha sido marcada como rechazada automáticamente.\n\n"
        f"ViveroOnline 🚀"
    )
    return await send_text_message(to, mensaje)
