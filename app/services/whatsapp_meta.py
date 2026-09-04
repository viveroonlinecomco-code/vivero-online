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
    """Obtener Phone Number ID desde variables de entorno."""
    return os.getenv("META_WA_PHONE_NUMBER_ID", "")


def _access_token() -> str:
    """Obtener Access Token desde variables de entorno."""
    return os.getenv("META_WA_ACCESS_TOKEN", "")


def _verify_token() -> str:
    """Obtener Verify Token para webhooks."""
    return os.getenv("WEBHOOK_VERIFY_TOKEN", "vivero_webhook_secure_token")


# ═══════════════════════════════════════════════════════════════
# FUNCIONES DE SEGURIDAD Y VALIDACIÓN
# ═══════════════════════════════════════════════════════════════


def verify_signature(body_bytes: bytes, signature_header: str) -> bool:
    """Valida firma HMAC de webhook Meta.
    
    Args:
        body_bytes: Body del request en bytes
        signature_header: Header X-Hub-Signature-256
    
    Returns:
        True si la firma es válido, False en caso contrario
    """
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
    """Descarga media de Meta en 2 pasos.
    
    Proceso:
    1. GET /v25.0/{media_id} con Auth → obtiene URL firmada
    2. GET URL real con Auth → bytes del archivo
    
    FIX: El paso 2 TAMBIÉN requiere Authorization header,
    sin él Meta devuelve 401 desde lookaside.fbsbx.com
    
    Args:
        media_id: ID del media en Meta
    
    Returns:
        Bytes del archivo descargado
    
    Raises:
        ValueError: Si no se puede obtener la URL o descargar
    """
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Paso 1: Obtener URL del media
        meta_resp = await client.get(
            f"{_GRAPH_API}/{media_id}",
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json().get("url")
        
        if not media_url:
            raise ValueError("No URL obtenida de Meta")
        
        logger.info(f"✅ URL obtenida para media {media_id}")
        
        # Paso 2: Descargar desde URL (también requiere Authorization)
        media_resp = await client.get(
            media_url,
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        media_resp.raise_for_status()
        
        logger.info(f"✅ Media descargado ({len(media_resp.content)} bytes)")
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
    """Busca planta → inventario → calcula precio cliente.
    
    Pasos:
    1. Busca en tabla 'plantas' por nombre_comun
    2. Obtiene planta_id
    3. Busca en 'v_inventario' con ese planta_id
    4. Obtiene precio_mayorista
    5. Calcula precio cliente con markup
    
    Args:
        supabase: Cliente Supabase
        producto_nombre: Nombre de la planta a buscar
        es_guest: Si es comprador guest (True) o registrado (False)
        plazo: Plazo de entrega ("inmediato" o similar)
    
    Returns:
        Mensaje con precio o error
    """
    try:
        logger.info(f"🔍 Buscando: {producto_nombre}")
        
        # 1. Buscar en tabla 'plantas'
        plantas_resp = supabase.table("plantas").select(
            "planta_id, nombre_comun"
        ).ilike("nombre_comun", f"%{producto_nombre}%").limit(1).execute()
        
        if not plantas_resp.data:
            logger.warning(f"Planta no encontrada: {producto_nombre}")
            return f"No encontré '{producto_nombre}'. Intenta con: Hiedra, Geranio, Duranta, Afelandra, Palma"
        
        planta = plantas_resp.data[0]
        planta_id = planta.get("planta_id")
        nombre_comun = planta.get("nombre_comun")
        logger.info(f"✅ Planta: {nombre_comun} (id={planta_id})")
        
        # 2. Buscar en 'v_inventario'
        inventario_resp = supabase.table("v_inventario").select(
            "inventario_id, precio_mayorista, stock"
        ).eq("planta_id", planta_id).limit(1).execute()
        
        if not inventario_resp.data:
            logger.warning(f"Sin inventario para planta_id={planta_id}")
            return f"'{nombre_comun}' no está disponible en inventario"
        
        inventario = inventario_resp.data[0]
        precio_mayorista = inventario.get("precio_mayorista", 0)
        
        if not precio_mayorista:
            return f"Error: '{nombre_comun}' sin precio"
        
        logger.info(f"✅ Precio mayorista: ${precio_mayorista}")
        
        # 3. Calcular precio con markup
        # Guest: ~18%, Registrado: ~15%
        if es_guest:
            precio_cliente = round(precio_mayorista * 1.18)
        else:
            precio_cliente = round(precio_mayorista * 1.15)
        
        precio_fmt = f"${int(precio_cliente):,.0f}".replace(",", ".")
        
        # 4. Construir respuesta
        mensaje = (
            f"Para tu proyecto, la {nombre_comun} "
            f"tiene un precio de {precio_fmt} COP. "
            f"Compra aquí: https://app.viveroonline.com.co/marketplace"
        )
        logger.info(f"✅ Respuesta enviada")
        return mensaje
    
    except Exception as e:
        logger.exception(f"❌ Error en procesar_consulta_precio_producto: {e}")
        return f"⚠️ Error: {str(e)[:100]}"


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
            logger.error(f"❌ Error {resp.status_code}: {resp.text}")
            return False
    
    except Exception as e:
        logger.error(f"❌ Excepción: {e}")
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
        
        if resp.status_code == 200:
            logger.info(f"✅ Template {template_name} enviado a {to_clean}")
            return True
        else:
            logger.error(f"❌ Error {resp.status_code}: {resp.text}")
            return False
    
    except Exception as e:
        logger.error(f"❌ Excepción: {e}")
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
        True si se envió exitosamente
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
    """Notifica al comprador que su cotización fue enviada.
    
    Args:
        to: WhatsApp del comprador
        nombre_comprador: Nombre del comprador
        proyecto: Nombre del proyecto
        total_cop: Monto total en COP
        num_viveristas: Número de viveristas a los que se envió
    
    Returns:
        True si se envió exitosamente
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
    """Notifica al comprador cuando un viverista responde.
    
    Args:
        to: WhatsApp del comprador
        nombre_comprador: Nombre del comprador
        proyecto: Nombre del proyecto
        nombre_viverista: Nombre del vivero que respondió
        monto_cop: Monto de la propuesta
    
    Returns:
        True si se envió exitosamente
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
        True si el token es válido
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
        True si la firma es válida
    """
    generated_sig = generate_webhook_signature(payload, app_secret)
    expected = f"sha256={generated_sig}"
    
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
