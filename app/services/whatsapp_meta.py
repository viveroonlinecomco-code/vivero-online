"""Cliente para enviar mensajes via Meta WhatsApp Cloud API y servicios de soporte."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Dict, Optional

import httpx
from app.services.precios import calcular_precios_pedido

logger = logging.getLogger(__name__)

_GRAPH_API = "https://graph.facebook.com/v25.0"


def _phone_id() -> str:
    return os.getenv("META_WA_PHONE_NUMBER_ID", "")


def _access_token() -> str:
    return os.getenv("META_WA_ACCESS_TOKEN", "")


async def send_text_message(to: str, body: str) -> bool:
    """Envía un mensaje de texto."""
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
            logger.info("✅ Mensaje enviado")
            return True
        logger.error("❌ Error %d", resp.status_code)
        return False
    except Exception as e:
        logger.exception("❌ Exception: %s", e)
        return False


async def send_template_message(
    to: str,
    template_name: str,
    language_code: str = "es",
    components: list | None = None,
) -> bool:
    """Envía template Meta."""
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
        return resp.status_code == 200
    except Exception as e:
        logger.exception("❌ Exception: %s", e)
        return False


async def download_media_bytes(media_id: str) -> bytes:
    """Descarga los bytes de un media file de Meta (imagen, audio, etc.)."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        meta_resp = await client.get(
            f"{_GRAPH_API}/{media_id}",
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json().get("url")
        if not media_url:
            raise ValueError(f"Meta no devolvió URL para media_id={media_id}")

        media_resp = await client.get(
            media_url,
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        media_resp.raise_for_status()
        return media_resp.content


def verify_signature(body_bytes: bytes, signature_header: str) -> bool:
    """Valida firma Meta."""
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


async def procesar_consulta_precio_producto(
    supabase,
    producto_nombre: str,
    es_guest: bool = True,
    plazo: str = "inmediato",
) -> str:
    """Busca en plantas → inventario → precio."""
    try:
        plantas_resp = supabase.table("plantas").select(
            "planta_id, nombre_comun"
        ).ilike("nombre_comun", f"%{producto_nombre}%").limit(1).execute()

        if not plantas_resp.data:
            return f"No encontré '{producto_nombre}'. Intenta con: Hiedra, Geranio, Duranta, Afelandra"

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
        inventario_id = inventario.get("inventario_id")

        if not precio_mayorista:
            return "Error: precio no disponible"

        resultado_precios = calcular_precios_pedido(
            cliente={
                "es_guest": es_guest,
                "cliente_id": None if es_guest else 0,
            },
            items=[{
                "inventario_id": inventario_id,
                "cantidad": 1,
                "precio_unitario": precio_mayorista,
            }],
            plazo=plazo,
            forzar_canal=None,
        )

        precio_cliente = resultado_precios["totales"]["precio_final_cliente"]
        precio_fmt = f"${int(precio_cliente):,.0f}".replace(",", ".")
        
        return (
            f"Para tu proyecto, la {nombre_comun} "
            f"tiene un precio de {precio_fmt} COP. "
            f"Compra: https://app.viveroonline.com.co/marketplace"
        )
    except Exception as e:
        logger.exception("❌ Error: %s", e)
        return f"⚠️ Error: {str(e)[:100]}"


async def notify_viverista_nueva_cotizacion(
    to: str,
    nombre_viverista: str,
    proyecto: str,
    cliente: str,
    tu_parte_cop: int,
    ciudad_entrega: str,
    horas_para_responder: int = 2,
) -> Dict[str, Any]:
    msg = f"🌿 Nueva solicitud\n{proyecto}\n💰 ${tu_parte_cop:,}"
    result = await send_text_message(to, msg)
    return {"ok": result}


async def notify_viverista_recordatorio(
    to: str,
    nombre_viverista: str,
    proyecto: str,
    tu_parte_cop: int = 0,
    numero_recordatorio: int = 1,
    minutos_restantes: int = 0,
) -> Dict[str, Any]:
    msg = f"⏰ Recordatorio {numero_recordatorio}\n{proyecto}"
    result = await send_text_message(to, msg)
    return {"ok": result}


async def notify_viverista_timeout(
    to: str,
    nombre_viverista: str,
    proyecto: str,
) -> Dict[str, Any]:
    msg = f"❌ Solicitud expirada\n{proyecto}"
    result = await send_text_message(to, msg)
    return {"ok": result}


async def notify_comprador_pedido_parcial(
    to: str,
    nombre_cliente: str,
    proyecto: str,
    monto_disponible_cop: int = 0,
    detalle_no_confirmado: str = "",
) -> Dict[str, Any]:
    msg = f"📋 Pedido parcial\n{proyecto}\n✅ ${monto_disponible_cop:,}"
    result = await send_text_message(to, msg)
    return {"ok": result}
