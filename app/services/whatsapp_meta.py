"""Cliente para enviar mensajes via Meta WhatsApp Cloud API.

Servicios disponibles:
- send_text_message(to, body)
- send_template_message(to, template_name, language_code, components)
- download_media_bytes(media_id)
- verify_signature(body_bytes, signature_header)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

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
        components: Variables de la plantilla (header, body, buttons)
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
            return True
        logger.error(
            "Meta send_template error %d: %s", resp.status_code, resp.text[:300]
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
