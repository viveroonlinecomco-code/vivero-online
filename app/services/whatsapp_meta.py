"""Cliente WhatsApp — Auto-detecta estructura de tabla inventario."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Dict

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
            logger.info(f"✅ Mensaje enviado")
            return True
        logger.error(f"❌ Error {resp.status_code}")
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
        if resp.status_code == 200:
            logger.info(f"✅ Template enviado")
            return True
        return False
    except Exception as e:
        logger.exception(f"❌ Exception: {e}")
        return False


async def download_media_bytes(media_id: str) -> bytes:
    """Descarga media de Meta."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        meta_resp = await client.get(
            f"{_GRAPH_API}/{media_id}",
            headers={"Authorization": f"Bearer {_access_token()}"},
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json().get("url")
        if not media_url:
            raise ValueError("No URL")

        media_resp = await client.get(media_url)
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


def _obtener_nombre_producto(row: dict) -> str:
    """Auto-detecta el nombre del producto en cualquier columna."""
    # Intenta varias columnas comunes
    for col in ["nombre_comun", "nombre", "product_name", "title", "name", "descripcion"]:
        if col in row and row[col]:
            return str(row[col])
    return "Producto"


def _obtener_id_producto(row: dict) -> int | None:
    """Auto-detecta el ID del producto."""
    for col in ["id", "inventario_id", "product_id", "producto_id"]:
        if col in row:
            val = row[col]
            if val:
                try:
                    return int(val)
                except:
                    pass
    return None


def _obtener_precio(row: dict) -> int | None:
    """Auto-detecta el precio mayorista."""
    for col in ["precio_mayorista", "precio_base", "price", "costo", "precio"]:
        if col in row:
            val = row[col]
            if val:
                try:
                    return int(val)
                except:
                    pass
    return None


async def procesar_consulta_precio_producto(
    supabase,
    producto_nombre: str,
    es_guest: bool = True,
    plazo: str = "inmediato",
) -> str:
    """✅ AUTO-DETECTA estructura de tabla."""
    try:
        logger.info(f"🔍 Buscando: {producto_nombre}")
        
        # 1. SELECT * para obtener toda la data
        productos = supabase.table("inventario").select(
            "*"
        ).limit(5).execute()
        
        if not productos.data:
            return "No hay productos en el catálogo"
        
        logger.info(f"Total productos: {len(productos.data)}")
        
        # 2. Buscar en Python (más flexible)
        producto_encontrado = None
        for row in productos.data:
            nombre = _obtener_nombre_producto(row)
            if producto_nombre.lower() in nombre.lower():
                producto_encontrado = row
                break
        
        if not producto_encontrado:
            return f"No encontré '{producto_nombre}'. Intenta con otros nombres."
        
        # 3. Extraer datos
        producto_id = _obtener_id_producto(producto_encontrado)
        precio_mayorista = _obtener_precio(producto_encontrado)
        nombre_final = _obtener_nombre_producto(producto_encontrado)
        
        logger.info(f"✅ Encontrado: {nombre_final} (id={producto_id}, precio=${precio_mayorista})")
        
        if not producto_id or not precio_mayorista:
            return "Error: datos incompletos"
        
        # 4. Calcular precio
        resultado_precios = calcular_precios_pedido(
            cliente={"es_guest": es_guest, "cliente_id": None if es_guest else 0},
            items=[{"inventario_id": producto_id, "cantidad": 1, "precio_unitario": precio_mayorista}],
            plazo=plazo,
            forzar_canal=None,
        )
        
        precio_cliente = resultado_precios["totales"]["precio_final_cliente"]
        logger.info(f"✅ Precio cliente: ${precio_cliente}")
        
        # 5. Mensaje
        precio_fmt = f"${int(precio_cliente):,.0f}".replace(",", ".")
        mensaje = (
            f"Para tu proyecto, la {nombre_final} "
            f"tiene un precio de {precio_fmt} COP. "
            f"Compra: https://app.viveroonline.com.co/marketplace"
        )
        return mensaje
        
    except Exception as e:
        logger.exception(f"❌ Error: {e}")
        return f"⚠️ Error: {str(e)[:100]}"


async def obtener_recomendacion_producto(
    supabase,
    producto_id: int,
    es_guest: bool = True,
    plazo: str = "inmediato",
) -> dict:
    """Obtiene recomendación."""
    try:
        inventario = supabase.table("inventario").select("*").eq("id", producto_id).single().execute()
        
        if not inventario.data:
            return {"error": "No encontrado"}
        
        datos = inventario.data
        nombre = _obtener_nombre_producto(datos)
        precio = _obtener_precio(datos)
        
        if not precio:
            return {"error": "Sin precio"}
        
        resultado_precios = calcular_precios_pedido(
            cliente={"es_guest": es_guest, "cliente_id": None},
            items=[{"inventario_id": producto_id, "cantidad": 1, "precio_unitario": precio}],
            plazo=plazo,
            forzar_canal=None,
        )
        
        precio_cliente = resultado_precios["totales"]["precio_final_cliente"]
        
        return {
            "id": producto_id,
            "nombre": nombre,
            "precio_cliente_cop": int(precio_cliente),
            "precio_mayorista_cop": precio,
            "error": None
        }
        
    except Exception as e:
        logger.exception(f"Error: {e}")
        return {"error": str(e)}


def _format_cop(monto) -> str:
    """Formato: $88.410"""
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
    """Notifica viverista."""
    msg = f"🌿 Nueva solicitud\n{proyecto}\n💰 ${tu_parte_cop:,}"
    result = await send_text_message(to, msg)
    return {"ok": result}


async def notify_viverista_recordatorio(
    to: str,
    nombre_viverista: str,
    proyecto: str,
    tu_parte_cop: int,
    numero_recordatorio: int,
    minutos_restantes: int,
) -> Dict[str, Any]:
    """Recordatorio viverista."""
    msg = f"⏰ Recordatorio {numero_recordatorio}\n{proyecto}"
    result = await send_text_message(to, msg)
    return {"ok": result}


async def notify_comprador_pedido_parcial(
    to: str,
    nombre_cliente: str,
    proyecto: str,
    monto_disponible_cop: int,
    detalle_no_confirmado: str,
) -> Dict[str, Any]:
    """Notifica comprador."""
    msg = f"📋 {proyecto}\n✅ ${monto_disponible_cop:,}"
    result = await send_text_message(to, msg)
    return {"ok": result}
