"""Cliente WhatsApp Meta — Notificaciones para viveristas y compradores.

IMPORTANTE: Este archivo NO importa de sí mismo. Todas las funciones
están definidas aquí. El módulo se puede importar sin problemas circulares.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

_GRAPH_API = "https://graph.facebook.com/v25.0"


def _phone_id() -> str:
    """Obtener Phone Number ID desde variables de entorno."""
    return os.getenv("META_WA_PHONE_NUMBER_ID", "")


def _access_token() -> str:
    """Obtener Access Token desde variables de entorno."""
    return os.getenv("META_WA_ACCESS_TOKEN", "")


def _verify_token() -> str:
    """Obtener Verify Token para webhooks."""
    return os.getenv("WEBHOOK_VERIFY_TOKEN", "vivero_webhook_secure_token")


# ═══════════════════════════════════════════════════════════════
# FUNCIONES PÚBLICAS — ENVÍO DE MENSAJES
# ═══════════════════════════════════════════════════════════════


async def send_text_message(to: str, body: str) -> bool:
    """Envía un mensaje de texto a través de Meta WhatsApp API.
    
    Args:
        to: Número de teléfono (puede incluir o no '+')
        body: Cuerpo del mensaje (máx 4096 caracteres)
    
    Returns:
        True si se envió exitosamente, False en caso contrario
    """
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
        
        if resp.status_code == 200:
            logger.info(f"✅ Mensaje de texto enviado a {to_clean}")
            return True
        else:
            logger.error(f"❌ Error {resp.status_code} enviando mensaje: {resp.text}")
            return False
    
    except Exception as e:
        logger.error(f"❌ Excepción en send_text_message: {e}")
        return False


async def send_template_message(
    to: str,
    template_name: str,
    language_code: str = "es_MX",
    parameters: Optional[list[Dict[str, Any]]] = None,
) -> bool:
    """Envía un mensaje de template (plantilla pre-aprobada).
    
    Args:
        to: Número de teléfono destino
        template_name: Nombre de la plantilla en Meta Business Manager
        language_code: Código de idioma (default: español México)
        parameters: Lista de dicts con 'type' y 'text' para parámetros
    
    Returns:
        True si se envió exitosamente, False en caso contrario
    """
    to_clean = to.lstrip("+")
    
    # Construir estructura de parámetros
    params_body = []
    if parameters:
        params_body = parameters
    
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
        
        if resp.status_code == 200:
            logger.info(f"✅ Template enviado a {to_clean}: {template_name}")
            return True
        else:
            logger.error(f"❌ Error {resp.status_code} enviando template: {resp.text}")
            return False
    
    except Exception as e:
        logger.error(f"❌ Excepción en send_template_message: {e}")
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
    """Notifica a un viverista sobre una nueva cotización.
    
    Args:
        to: WhatsApp del viverista (con o sin +)
        nombre_viverista: Nombre del vivero
        proyecto: Nombre del proyecto del cliente
        cliente: Nombre del cliente
        tu_parte_cop: Monto en COP que le corresponde al viverista
        ciudad_entrega: Ciudad de entrega
        horas_para_responder: Horas para responder (default: 2)
    
    Returns:
        True si se envió exitosamente, False en caso contrario
    """
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
    """Notifica al comprador que su cotización fue enviada a viveristas.
    
    Args:
        to: WhatsApp del comprador
        nombre_comprador: Nombre del comprador
        proyecto: Nombre del proyecto
        total_cop: Monto total en COP
        num_viveristas: Número de viveristas a los que se envió
    
    Returns:
        True si se envió exitosamente, False en caso contrario
    """
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
    """Notifica al comprador cuando un viverista responde a su cotización.
    
    Args:
        to: WhatsApp del comprador
        nombre_comprador: Nombre del comprador
        proyecto: Nombre del proyecto
        nombre_viverista: Nombre del vivero que respondió
        monto_cop: Monto de la propuesta
    
    Returns:
        True si se envió exitosamente, False en caso contrario
    """
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
    """Verifica que el token de webhook sea válido.
    
    Args:
        received_token: Token recibido en el challenge
        expected_token: Token esperado (default: variable de entorno)
    
    Returns:
        True si el token es válido, False en caso contrario
    """
    if expected_token is None:
        expected_token = _verify_token()
    
    return received_token == expected_token


def generate_webhook_signature(
    payload: str,
    app_secret: Optional[str] = None,
) -> str:
    """Genera la firma HMAC para validar webhooks de Meta.
    
    Args:
        payload: Body del request como string
        app_secret: App Secret de Meta (default: variable de entorno)
    
    Returns:
        Firma HMAC hexadecimal
    """
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
    """Verifica la firma HMAC de un webhook de Meta.
    
    Args:
        payload: Body del request como string
        received_signature: Firma en header X-Hub-Signature-256
        app_secret: App Secret de Meta (default: variable de entorno)
    
    Returns:
        True si la firma es válida, False en caso contrario
    """
    generated_sig = generate_webhook_signature(payload, app_secret)
    expected = f"sha256={generated_sig}"
    
    # Comparación segura (constant time)
    return hmac.compare_digest(expected, received_signature)


# ═══════════════════════════════════════════════════════════════
# PARSEO DE EVENTOS DE WEBHOOK
# ═══════════════════════════════════════════════════════════════


def parse_webhook_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extrae el primer evento de mensaje de un payload de webhook.
    
    Args:
        payload: Payload completo del webhook
    
    Returns:
        Diccionario con información del evento o None
    """
    try:
        # Estructura: { "entry": [{ "changes": [{ "value": { "messages": [...] } }] }] }
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
    """Extrae información útil de un objeto de mensaje.
    
    Args:
        message_obj: Objeto de mensaje del webhook
    
    Returns:
        Dict con from, type, text, timestamp o None
    """
    try:
        msg_type = message_obj.get("type", "")
        
        # Solo procesar mensajes de texto
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
