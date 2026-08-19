"""Cliente para enviar mensajes via Meta WhatsApp Cloud API.

VERSIÓN CORREGIDA — Usa columnas correctas de inventario.
"""
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
            logger.info(f"✅ Mensaje enviado a {to}")
            return True
        logger.error(f"❌ Error {resp.status_code}: {resp.text[:300]}")
        return False
    except Exception as e:
        logger.exception(f"❌ Exception: {e}")
        return False


async def send_template_message(
    to: str,
    template_name: str,
    language_code: str = "es",
    components: list | None = None,
) -> bool:
    """Envía un mensaje usando una plantilla Meta aprobada."""
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
            logger.info(f"✅ Template {template_name} enviado")
            return True
        logger.error(f"❌ Error {resp.status_code}")
        return False
    except Exception as e:
        logger.exception(f"❌ Exception: {e}")
        return False


async def download_media_bytes(media_id: str) -> bytes:
    """Descarga bytes de un media file de Meta."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        meta_resp = await client.get(
            f"{_GRAPH_API}/{media_id}",
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json().get("url")
        if not media_url:
            raise ValueError(f"Meta no devolvió URL")

        media_resp = await client.get(
            media_url,
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        media_resp.raise_for_status()
        return media_resp.content


def verify_signature(body_bytes: bytes, signature_header: str) -> bool:
    """Valida la firma x-hub-signature-256 de Meta."""
    app_secret = os.getenv("META_WA_APP_SECRET", "")
    if not app_secret:
        logger.warning("META_WA_APP_SECRET no configurado")
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
    """✅ CORREGIDO — Procesa "PRECIO [PRODUCTO]" con matriz comercial correcta.
    
    Busca producto por nombre_comun y retorna precio final cliente.
    """
    try:
        logger.info(f"🔍 Buscando producto: {producto_nombre}")
        
        # 1. Buscar producto por nombre_comun (sin especificar id si no existe)
        productos = supabase.table("inventario").select(
            "*"
        ).ilike("nombre_comun", f"%{producto_nombre}%").limit(1).execute()
        
        if not productos.data:
            logger.warning(f"Producto no encontrado: {producto_nombre}")
            return f"No encontré '{producto_nombre}'. Intenta con: Hiedra, Geranio, Duranta, Afelandra"
        
        producto = productos.data[0]
        logger.info(f"✅ Producto encontrado: {producto.get('nombre_comun')}")
        
        # Obtener ID (puede ser 'id' o 'inventario_id')
        producto_id = producto.get("id") or producto.get("inventario_id")
        precio_mayorista = producto.get("precio_mayorista", 0)
        
        if not producto_id or not precio_mayorista:
            logger.error(f"Datos incompletos: id={producto_id}, precio={precio_mayorista}")
            return "Error: datos incompletos del producto"
        
        logger.info(f"Precio mayorista: ${precio_mayorista}")
        
        # 2. Calcular precio con matriz comercial
        resultado_precios = calcular_precios_pedido(
            cliente={
                "es_guest": es_guest,
                "cliente_id": None if es_guest else 0,
            },
            items=[{
                "inventario_id": producto_id,
                "cantidad": 1,
                "precio_unitario": precio_mayorista,
            }],
            plazo=plazo,
            forzar_canal=None,
        )
        
        # 3. Extraer precio final
        precio_cliente = resultado_precios["totales"]["precio_final_cliente"]
        logger.info(f"✅ Precio cliente: ${precio_cliente}")
        
        # 4. Construir nombre
        nombre_final = producto.get("nombre_comun", "Producto")
        
        # 5. Construir mensaje
        precio_formateado = f"${int(precio_cliente):,.0f}".replace(",", ".")
        canal_str = "tu proyecto"
        
        mensaje = (
            f"Para {canal_str}, la {nombre_final} "
            f"tiene un precio de {precio_formateado} COP. "
            f"Compra aquí: https://app.viveroonline.com.co/marketplace/producto/{producto_id}"
        )
        
        logger.info(f"✅ Respuesta: {mensaje[:50]}")
        return mensaje
        
    except Exception as e:
        logger.exception(f"❌ Error procesar_consulta_precio: {e}")
        return f"⚠️ Error al consultar precio: {str(e)}"


async def obtener_recomendacion_producto(
    supabase,
    producto_id: int,
    es_guest: bool = True,
    plazo: str = "inmediato",
) -> dict:
    """Obtiene recomendación con precio correcto."""
    try:
        inventario = supabase.table("inventario").select(
            "*"
        ).eq("id", producto_id).single().execute()
        
        if not inventario.data:
            return {"error": f"Producto no encontrado"}
        
        datos = inventario.data
        precio_mayorista = datos.get("precio_mayorista", 0)
        
        resultado_precios = calcular_precios_pedido(
            cliente={
                "es_guest": es_guest,
                "cliente_id": None if es_guest else 0,
            },
            items=[{
                "inventario_id": producto_id,
                "cantidad": 1,
                "precio_unitario": precio_mayorista,
            }],
            plazo=plazo,
            forzar_canal=None,
        )
        
        precio_cliente = resultado_precios["totales"]["precio_final_cliente"]
        nombre_final = datos.get("nombre_comun", "Producto")
        
        return {
            "id": producto_id,
            "nombre": nombre_final,
            "precio_cliente_cop": int(precio_cliente),
            "precio_mayorista_cop": precio_mayorista,
            "canal": resultado_precios["canal"],
            "plazo": resultado_precios["plazo"],
            "error": None
        }
        
    except Exception as e:
        logger.exception(f"Error obtener_recomendacion_producto: {e}")
        return {"error": f"Error: {str(e)}"}


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
    """Notificación al viverista con cotización nueva."""
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
    
    text_result = await send_text_message(to, msg_texto)
    return {"ok": text_result, "message": "Notificación enviada"}


async def notify_viverista_recordatorio(
    to: str,
    nombre_viverista: str,
    proyecto: str,
    tu_parte_cop: int,
    numero_recordatorio: int,
    minutos_restantes: int,
) -> Dict[str, Any]:
    """Recordatorio de cotización sin respuesta."""
    msg_texto = (
        f"⏰ *RECORDATORIO — ViveroOnline*\n\n"
        f"Proyecto: *{proyecto}*\n"
        f"Tu precio: {_format_cop(tu_parte_cop)} COP\n\n"
        f"Recordatorio {numero_recordatorio}/3\n"
        f"⏱️ {minutos_restantes} minutos para responder"
    )
    
    text_result = await send_text_message(to, msg_texto)
    return {"ok": text_result}


async def notify_comprador_pedido_parcial(
    to: str,
    nombre_cliente: str,
    proyecto: str,
    monto_disponible_cop: int,
    detalle_no_confirmado: str,
) -> Dict[str, Any]:
    """Notificación al comprador con cotización parcial."""
    msg_texto = (
        f"📋 *ACTUALIZACIÓN DE TU PEDIDO — ViveroOnline*\n\n"
        f"Hola {nombre_cliente},\n\n"
        f"Proyecto: *{proyecto}*\n\n"
        f"✅ Disponible: {_format_cop(monto_disponible_cop)} COP\n"
        f"❌ No confirmado: {detalle_no_confirmado}"
    )
    
    text_result = await send_text_message(to, msg_texto)
    return {"ok": text_result}
