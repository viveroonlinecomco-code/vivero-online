"""Cliente para enviar mensajes via Meta WhatsApp Cloud API.

Servicios:
- send_text_message() — envía texto
- send_template_message() — envía template Meta
- procesar_consulta_precio_producto() — consulta precio con matriz comercial
- obtener_recomendacion_producto() — obtiene recomendación de producto
- download_media_bytes() — descarga media
- verify_signature() — valida firma Meta
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
    """Envía un mensaje de texto.
    
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


async def obtener_recomendacion_producto(
    supabase,
    producto_id: int,
    es_guest: bool = True,
    plazo: str = "inmediato",
) -> dict:
    """Obtiene recomendación de producto con precio correcto (matriz comercial).
    
    Ejemplo B2C (guest, inmediato):
        Hiedra: $17.010 × 1.20 (markup) = $20.412 ✅
    
    Ejemplo B2B (registrado, >= 5 SMLMV, inmediato):
        Hiedra: $17.010 × 1.20 × (1 - 0.12 descuento) = $17.962 ✅
    """
    try:
        # 1. Consultar BD
        inventario = supabase.table("inventario").select(
            "id, precio_mayorista, categoria_producto, nombre_comun, nombre_cientifico, vivero_id"
        ).eq("id", producto_id).single().execute()
        
        if not inventario.data:
            return {"error": f"Producto {producto_id} no encontrado"}
        
        datos = inventario.data
        precio_mayorista = datos.get("precio_mayorista", 0)
        
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
        
        # 4. Construir nombre
        nombre_final = datos.get("nombre_comun", "Producto")
        nombre_cientifico = datos.get("nombre_cientifico", "")
        
        if nombre_cientifico and nombre_cientifico != nombre_final:
            nombre_final = f"{nombre_final} ({nombre_cientifico})"
        
        # 5. Retornar
        return {
            "id": producto_id,
            "nombre": nombre_final,
            "precio_cliente_cop": int(precio_cliente),
            "precio_mayorista_cop": precio_mayorista,
            "canal": resultado_precios["canal"],
            "plazo": resultado_precios["plazo"],
            "vivero_id": datos.get("vivero_id"),
            "error": None
        }
        
    except Exception as e:
        logger.exception(f"Error obtener_recomendacion_producto: {e}")
        return {"error": f"Error: {str(e)}"}


async def procesar_consulta_precio_producto(
    supabase,
    producto_nombre: str,
    es_guest: bool = True,
    plazo: str = "inmediato",
) -> str:
    """Procesa "PRECIO [PRODUCTO]" con matriz comercial correcta.
    
    Retorna mensaje con precio para B2C o B2B.
    """
    try:
        # 1. Buscar producto
        productos = supabase.table("inventario").select(
            "id, precio_mayorista, categoria_producto, nombre_comun"
        ).ilike("nombre_comun", f"%{producto_nombre}%").limit(1).execute()
        
        if not productos.data:
            return f"No encontré '{producto_nombre}'. Intenta con: Hiedra, Geranio, Duranta, Afelandra"
        
        producto_id = productos.data[0]["id"]
        
        # 2. Obtener recomendación con precio correcto
        recom = await obtener_recomendacion_producto(
            supabase=supabase,
            producto_id=producto_id,
            es_guest=es_guest,
            plazo=plazo,
        )
        
        if "error" in recom and recom["error"]:
            return f"Error: {recom['error']}"
        
        # 3. Construir mensaje
        precio_formateado = f"${recom['precio_cliente_cop']:,.0f}".replace(",", ".")
        canal_str = "tu proyecto" if recom['canal'] == 'b2c' else "tu negocio"
        
        mensaje = (
            f"Para {canal_str}, la {recom['nombre']} "
            f"tiene un precio de {precio_formateado} COP. "
            f"Compra aquí: https://app.viveroonline.com.co/marketplace/producto/{producto_id}"
        )
        
        return mensaje
        
    except Exception as e:
        logger.exception(f"Error procesar_consulta_precio: {e}")
        return f"Error: {str(e)}"


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
    
    return {
        "ok": text_result,
        "message": "Notificación enviada"
    }


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
        f"⏱️ {minutos_restantes} minutos para responder\n\n"
        f"¿Confirmás disponibilidad?\n"
        f"Respondé *APROBAR* o *RECHAZAR*"
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
        f"✅ Disponible para procesar: {_format_cop(monto_disponible_cop)} COP\n"
        f"❌ No confirmado por vivero: {detalle_no_confirmado}\n\n"
        f"Ingresá al panel para:\n"
        f"• Pagar lo disponible\n"
        f"• Buscar vivero alternativo\n"
        f"• Cancelar pedido\n\n"
        f"Tu pedido está protegido. 🌿"
    )
    
    text_result = await send_text_message(to, msg_texto)
    
    return {"ok": text_result}
