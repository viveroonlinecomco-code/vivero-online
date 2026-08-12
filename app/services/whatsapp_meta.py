"""Cliente para enviar mensajes via Meta WhatsApp Cloud API.

Servicios disponibles:
- send_text_message(to, body)
- send_template_message(to, template_name, language_code, components)
- download_media_bytes(media_id)
- verify_signature(body_bytes, signature_header)

CORRECCIONES (7 ago 2026):
- Funciones notify_viverista_* y notify_comprador_* son ahora ASYNC
- send_template_message() acepta componentes en formato Meta correcto
- Variables de template van en components[0]["parameters"]["body"]["text"]
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Dict, Any

import httpx

logger = logging.getLogger(__name__)

# Versión de la Graph API (según la que Meta te mostró en el curl de prueba)
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
            logger.info(f"✅ Mensaje texto enviado a {to_clean}")
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
        components: Componentes en formato Meta (body con variables, botones, etc)
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
            logger.info(f"✅ Template {template_name} enviado a {to_clean}")
            return True
        logger.error(
            "Meta send_template error %d template=%s: %s", 
            resp.status_code, template_name, resp.text[:300]
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
# TEMPLATES FEATURE AUTO-TIMEOUT (5 ago 2026) — CORREGIDAS 7 ago
# ═══════════════════════════════════════════════════════════════════════════
# 3 templates aprobados por Meta el 5 ago 2026:
#   1. notif_viverista_nueva_cotizacion  (6 vars body, 3 botones Quick Reply)
#   2. recordatorio_viverista_pendiente  (5 vars body, 2 botones Quick Reply)
#   3. notif_comprador_pedido_parcial    (4 vars body, 1 botón URL estática)
#
# Correcciones:
# - Todas las funciones son ASYNC (await send_template_message)
# - Variables van en format Meta: components[0]["parameters"]["body"]["text"]
# ═══════════════════════════════════════════════════════════════════════════

def _format_cop(monto) -> str:
    """Formato monto Colombia: 88410 -> '$88.410 COP'"""
    monto = int(float(monto) if monto else 0)
    return f"${monto:,} COP".replace(",", ".")


async def notify_viverista_nueva_cotizacion(
    to: str,
    nombre_viverista: str,
    proyecto: str,
    cliente: str,
    tu_parte_cop,
    ciudad_entrega: str,
    horas_para_responder: int = 2,
) -> bool:
    """Template 1 — notificación inicial al viverista con cotización nueva.
    Se envía al crear una cotización que involucra a este vivero.
    Botones (Quick Reply, estáticos): APROBAR, RECHAZAR, VER DETALLE
    """
    components = [
        {
            "type": "body",
            "parameters": {
                "text": [
                    nombre_viverista,
                    proyecto,
                    cliente,
                    _format_cop(tu_parte_cop),
                    ciudad_entrega,
                    str(horas_para_responder),
                ]
            },
        }
    ]
    return await send_template_message(
        to=to,
        template_name="notif_viverista_nueva_cotizacion",
        components=components,
    )


async def notify_viverista_recordatorio(
    to: str,
    nombre_viverista: str,
    proyecto: str,
    tu_parte_cop,
    numero_recordatorio: int,
    minutos_restantes: int,
) -> bool:
    """Template 2 — recordatorio de cotización sin respuesta.
    Enviado por auto_timeout.py según cadencia:
      - Normal: 30 / 60 / 90 min desde creación
      - Materas: 60 / 120 / 180 min (grace period)
    Botones (Quick Reply): APROBAR, RECHAZAR
    """
    components = [
        {
            "type": "body",
            "parameters": {
                "text": [
                    nombre_viverista,
                    proyecto,
                    _format_cop(tu_parte_cop),
                    str(numero_recordatorio),
                    str(minutos_restantes),
                ]
            },
        }
    ]
    return await send_template_message(
        to=to,
        template_name="recordatorio_viverista_pendiente",
        components=components,
    )


async def notify_comprador_pedido_parcial(
    to: str,
    nombre_cliente: str,
    proyecto: str,
    monto_disponible_cop,
    detalle_no_confirmado: str,
) -> bool:
    """Template 3 — notificación al comprador con cotización parcial.
    Se envía cuando auto_timeout marca una sub_cotización como rechazada
    por sin respuesta del viverista dentro del plazo.
    Botón (URL estática): Ver mi pedido → /comprador
    """
    components = [
        {
            "type": "body",
            "parameters": {
                "text": [
                    nombre_cliente,
                    proyecto,
                    _format_cop(monto_disponible_cop),
                    detalle_no_confirmado,
                ]
            },
        }
    ]
    return await send_template_message(
        to=to,
        template_name="notif_comprador_pedido_parcial",
        components=components,
    )
