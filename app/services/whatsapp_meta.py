"""Cliente para enviar mensajes via Meta WhatsApp Cloud API.

Servicios disponibles:
- send_text_message(to, body) — async
- send_template_message(to, template_name, language_code, components) — async
- notify_viverista_nueva_cotizacion(to, ...) — async
- notify_viverista_recordatorio(to, ...) — async
- notify_comprador_pedido_parcial(to, ...) — async
- download_media_bytes(media_id) — async
- verify_signature(body_bytes, signature_header) — sync
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Dict, Optional

import httpx

# FIX 18 AGO 2026 - AGREGAR ESTE IMPORT
from app.services.precios import calcular_precios_pedido
from app.services.config_global import get_config, get_matriz_comercial

logger = logging.getLogger(__name__)

# Versión de la Graph API
_GRAPH_API = "https://graph.facebook.com/v25.0"


def _phone_id() -> str:
    return os.getenv("META_WA_PHONE_NUMBER_ID", "")


def _access_token() -> str:
    return os.getenv("META_WA_ACCESS_TOKEN", "")


async def send_text_message(to: str, body: str) -> bool:
    """Envía un mensaje de texto plano a un número WhatsApp.

    Args:
        to: Número en formato E.164 (ej: '+573178543819')
        body: Texto a enviar (max 4096 chars)

    Returns:
        True si se envió, False si hubo error.
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
            logger.info(f"Texto enviado a {to}")
            return True
        logger.error("Meta send_text error %d: %s", resp.status_code, resp.text[:300])
        return False
    except Exception as e:
        logger.exception(f"Meta send_text exception: {e}")
        return False


async def send_template_message(
    to: str,
    template_name: str,
    language_code: str = "es",
    components: list | None = None,
) -> bool:
    """Envía un mensaje usando una plantilla aprobada por Meta.

    Usar para notificaciones business-initiated (ej: orden pagada).

    Args:
        to: Número del destinatario
        template_name: Nombre exacto de la plantilla en Meta Business Manager
        language_code: 'es', 'en_US', etc.
        components: Variables de la plantilla (estructura Meta: [{"type":"body","parameters":[{"type":"text","text":"..."}]}])
    """
    to_clean = to.lstrip("+")
    payload = {
        "messaging_product": "whatsapp",
        "to": to_clean,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }
    if components:
        payload["template"]["components"] = components

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
            logger.info(f"Template {template_name} enviado a {to}")
            return True
        logger.error(
            "Meta send_template error %d for %s: %s", resp.status_code, template_name, resp.text[:300]
        )
        return False
    except Exception as e:
        logger.exception(f"Meta send_template exception: {e}")
        return False


async def download_media_bytes(media_id: str) -> bytes:
    """Descarga los bytes de un media file de Meta (imagen, audio, etc.)

    Proceso 2 pasos:
    1. GET /v25.0/{media_id} → obtiene URL real con token
    2. GET URL real → bytes del archivo

    Raises:
        Exception si no se puede obtener la URL o descargar.
    """
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Paso 1: obtener URL real
        meta_resp = await client.get(
            f"{_GRAPH_API}/{media_id}",
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json().get("url")
        if not media_url:
            raise ValueError(f"Meta no devolvió URL para media_id={media_id}")

        # Paso 2: descargar desde URL real
        media_resp = await client.get(
            media_url,
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        media_resp.raise_for_status()
        return media_resp.content


def verify_signature(body_bytes: bytes, signature_header: str) -> bool:
    """Valida la firma x-hub-signature-256 de Meta usando HMAC-SHA256 + App Secret.

    Returns:
        True si válida. True también si META_WA_APP_SECRET no está configurado
        (así no bloqueamos en dev). False si la firma no coincide.
    """
    app_secret = os.getenv("META_WA_APP_SECRET", "")
    if not app_secret:
        logger.warning("META_WA_APP_SECRET no configurado — saltando validación de firma")
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


# ═══════════════════════════════════════════════════════════════════════════
# TEMPLATES FEATURE AUTO-TIMEOUT (12 ago 2026 — CORREGIDO)
# ═══════════════════════════════════════════════════════════════════════════
# 3 templates aprobados por Meta:
#   1. notif_viverista_nueva_cotizacion  (6 vars body, 3 botones Quick Reply)
#   2. recordatorio_viverista_pendiente  (5 vars body, 2 botones Quick Reply)
#   3. notif_comprador_pedido_parcial    (4 vars body, 1 botón URL estática)
#
# CORREGIDO: Todas las funciones ahora son ASYNC y usan formato Meta v25.0
# ═══════════════════════════════════════════════════════════════════════════

def _format_cop(monto) -> str:
    """Formato monto Colombia: 88410 -> $88.410"""
    return f"${int(monto):,}".replace(",", ".")


async def notify_viverista_nueva_cotizacion(
    to: str,
    nombre_viverista: str,
    proyecto: str,
    cliente: str,
    tu_parte_cop: int,
    ciudad_entrega: str,
    horas_para_responder: int = 2,
) -> Dict[str, Any]:
    """Notificación inicial al viverista con cotización nueva.
    
    ESTRATEGIA:
    1. INTENTA template Meta (si falla, log pero continúa)
    2. SIEMPRE envía texto libre como fallback (garantizado)
    
    Así recuperamos funcionalidad conocida (texto) + agregamos Meta.
    """
    # Construir mensaje de texto (BASE — GARANTIZADO)
    msg_texto = (
        f"🌿 *Nueva solicitud — ViveroOnline*\n\n"
        f"Proyecto: *{proyecto}*\n"
        f"Solicitante: *{cliente}*\n"
        f"📦 {nombre_viverista}\n\n"
        f"💰 Tu precio: {_format_cop(tu_parte_cop)} COP\n\n"
        f"Zona: {ciudad_entrega}\n"
        f"⏰ Responde en {horas_para_responder}h\n\n"
        f"¿Confirmás disponibilidad?\n"
        f"Respondé *APROBAR* o *RECHAZAR*"
    )
    
    # INTENTAR template Meta (best-effort)
    template_result = False
    try:
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": nombre_viverista},
                    {"type": "text", "text": proyecto},
                    {"type": "text", "text": cliente},
                    {"type": "text", "text": _format_cop(tu_parte_cop)},
                    {"type": "text", "text": ciudad_entrega},
                    {"type": "text", "text": str(horas_para_responder)},
                ]
            }
        ]
        
        template_result = await send_template_message(
            to=to,
            template_name="notif_viverista_nueva_cotizacion",
            language_code="es",
            components=components,
        )
        if template_result:
            logger.info(f"✅ Template Meta enviado a {to}")
    except Exception as e:
        logger.warning(f"⚠️ Template Meta falló (seguimos con texto): {e}")
    
    # SIEMPRE enviar texto (fallback garantizado)
    text_result = await send_text_message(to, msg_texto)
    
    return {
        "ok": text_result,
        "template_meta": template_result,
        "text": text_result,
        "message": "Notificación enviada (texto garantizado)"
    }


async def notify_viverista_recordatorio(
    to: str,
    nombre_viverista: str,
    proyecto: str,
    tu_parte_cop: int,
    numero_recordatorio: int,
    minutos_restantes: int,
) -> Dict[str, Any]:
    """Recordatorio de cotización sin respuesta.
    
    Misma estrategia: TEXTO garantizado + template Meta best-effort
    """
    msg_texto = (
        f"⏰ *RECORDATORIO — ViveroOnline*\n\n"
        f"Proyecto: *{proyecto}*\n"
        f"Tu precio: {_format_cop(tu_parte_cop)} COP\n\n"
        f"Recordatorio {numero_recordatorio}/3\n"
        f"⏱️ {minutos_restantes} minutos para responder\n\n"
        f"¿Confirmás disponibilidad?\n"
        f"Respondé *APROBAR* o *RECHAZAR*"
    )
    
    template_result = False
    try:
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": nombre_viverista},
                    {"type": "text", "text": proyecto},
                    {"type": "text", "text": _format_cop(tu_parte_cop)},
                    {"type": "text", "text": str(numero_recordatorio)},
                    {"type": "text", "text": str(minutos_restantes)},
                ]
            }
        ]
        
        template_result = await send_template_message(
            to=to,
            template_name="recordatorio_viverista_pendiente",
            language_code="es",
            components=components,
        )
    except Exception as e:
        logger.warning(f"⚠️ Template recordatorio falló: {e}")
    
    text_result = await send_text_message(to, msg_texto)
    
    return {
        "ok": text_result,
        "template_meta": template_result,
        "text": text_result
    }


async def notify_comprador_pedido_parcial(
    to: str,
    nombre_cliente: str,
    proyecto: str,
    monto_disponible_cop: int,
    detalle_no_confirmado: str,
) -> Dict[str, Any]:
    """Notificación al comprador con cotización parcial.
    
    Misma estrategia: TEXTO garantizado + template Meta best-effort
    """
    msg_texto = (
        f"📋 *ACTUALIZACIÓN DE TU PEDIDO — ViveroOnline*\n\n"
        f"Hola {nombre_cliente},\n\n"
        f"Proyecto: *{proyecto}*\n\n"
        f"✅ Disponible para procesar: {_format_cop(monto_disponible_cop)} COP\n"
        f"❌ No confirmado por vivero: {detalle_no_confirmado}\n\n"
        f"Ingresá al panel para:\n"
        f"• Pagar lo disponible\n"
        f"• Buscar vivero alternativo\n"
        f"• Cancelar pedido\n\n"
        f"Tu pedido está protegido. 🌿"
    )
    
    template_result = False
    try:
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": nombre_cliente},
                    {"type": "text", "text": proyecto},
                    {"type": "text", "text": _format_cop(monto_disponible_cop)},
                    {"type": "text", "text": detalle_no_confirmado},
                ]
            }
        ]
        
        template_result = await send_template_message(
            to=to,
            template_name="notif_comprador_pedido_parcial",
            language_code="es",
            components=components,
        )
    except Exception as e:
        logger.warning(f"⚠️ Template comprador falló: {e}")
    
    text_result = await send_text_message(to, msg_texto)
    
    return {
        "ok": text_result,
        "template_meta": template_result,
        "text": text_result
    }
