"""Executor de acciones confirmadas por el viverista vía WhatsApp.

Recibe una acción del copilot (ya confirmada por el usuario) y la ejecuta
directamente en Supabase usando los endpoints internos existentes.

Acciones Sprint 1:
- actualizar_precio    → PATCH inventario.precio_mayorista
- actualizar_stock     → PATCH inventario.stock
- actualizar_estado    → PATCH inventario.estado_planta
- agregar_producto     → POST catalogo/guardar
- aprobar_pedido       → POST pedidos/{id}/aprobar
- rechazar_pedido      → POST pedidos/{id}/rechazar
"""
from __future__ import annotations
import logging
from typing import Any

from app.services.supabase import admin

logger = logging.getLogger(__name__)

# ─── Resultados posibles ───────────────────────────────────────────────────────

class AccionResultado:
    def __init__(self, ok: bool, mensaje: str, datos: dict | None = None):
        self.ok = ok
        self.mensaje = mensaje
        self.datos = datos or {}


# ─── Executor principal ────────────────────────────────────────────────────────

def ejecutar_accion(accion: dict, vivero_id: int, user_id: str) -> AccionResultado:
    """
    Ejecuta una acción confirmada por el viverista.

    Args:
        accion: {"type": "actualizar_precio", "params": {...}}
        vivero_id: ID del vivero del usuario
        user_id: ID del usuario en Supabase Auth

    Returns:
        AccionResultado con ok, mensaje y datos opcionales
    """
    tipo = accion.get("type", "")
    params = accion.get("params", {})

    logger.info(f"Ejecutando acción {tipo} para vivero {vivero_id}: {params}")

    try:
        if tipo == "actualizar_precio":
            return _actualizar_precio(params, vivero_id)
        elif tipo == "actualizar_stock":
            return _actualizar_stock(params, vivero_id)
        elif tipo == "actualizar_estado":
            return _actualizar_estado(params, vivero_id)
        elif tipo == "agregar_producto":
            return _agregar_producto(params, vivero_id)
        elif tipo == "aprobar_pedido":
            return _aprobar_pedido(params, vivero_id)
        elif tipo == "rechazar_pedido":
            return _rechazar_pedido(params, vivero_id)
        else:
            return AccionResultado(
                ok=False,
                mensaje=f"Acción '{tipo}' no reconocida."
            )
    except Exception as e:
        logger.exception(f"Error ejecutando acción {tipo}: {e}")
        return AccionResultado(
            ok=False,
            mensaje="Hubo un problema ejecutando la acción. Intentá de nuevo."
        )


# ─── Helpers de verificación ───────────────────────────────────────────────────

def _verificar_ownership(db, inventario_id: int, vivero_id: int) -> bool:
    """Verifica que el item pertenece al vivero del usuario."""
    resp = db.table("inventario").select("vivero_id").eq(
        "inventario_id", inventario_id
    ).limit(1).execute()
    if not resp.data:
        return False
    return resp.data[0]["vivero_id"] == vivero_id


def _nombre_planta(db, inventario_id: int) -> str:
    """Obtiene el nombre de la planta dado un inventario_id."""
    try:
        resp = db.table("inventario").select(
            "plantas(nombre_comun)"
        ).eq("inventario_id", inventario_id).limit(1).execute()
        if resp.data and resp.data[0].get("plantas"):
            return resp.data[0]["plantas"].get("nombre_comun", "la planta")
    except Exception:
        pass
    return "la planta"


# ─── Acciones individuales ─────────────────────────────────────────────────────

def _actualizar_precio(params: dict, vivero_id: int) -> AccionResultado:
    """Actualiza el precio mayorista de un item del inventario."""
    inventario_id = params.get("inventario_id")
    nuevo_precio = params.get("nuevo_precio")

    if not inventario_id or nuevo_precio is None:
        return AccionResultado(ok=False, mensaje="Faltan datos para actualizar el precio.")

    if nuevo_precio < 0:
        return AccionResultado(ok=False, mensaje="El precio no puede ser negativo.")

    db = admin()

    if not _verificar_ownership(db, inventario_id, vivero_id):
        return AccionResultado(ok=False, mensaje="Este item no pertenece a tu vivero.")

    nombre = params.get("nombre") or _nombre_planta(db, inventario_id)
    precio_comprador = round(nuevo_precio * 1.18)

    resp = db.table("inventario").update({
        "precio_mayorista": nuevo_precio,
    }).eq("inventario_id", inventario_id).execute()

    if not resp.data:
        return AccionResultado(ok=False, mensaje="No se pudo actualizar el precio.")

    return AccionResultado(
        ok=True,
        mensaje=(
            f"✅ *{nombre}* actualizado\n"
            f"• Tu precio: ${nuevo_precio:,.0f} COP\n"
            f"• Comprador paga: ${precio_comprador:,.0f} COP\n"
            f"Ya está visible en el marketplace."
        ),
        datos={"inventario_id": inventario_id, "nuevo_precio": nuevo_precio}
    )


def _actualizar_stock(params: dict, vivero_id: int) -> AccionResultado:
    """Actualiza el stock de un item del inventario."""
    inventario_id = params.get("inventario_id")
    nuevo_stock = params.get("nuevo_stock")

    if not inventario_id or nuevo_stock is None:
        return AccionResultado(ok=False, mensaje="Faltan datos para actualizar el stock.")

    if nuevo_stock < 0:
        return AccionResultado(ok=False, mensaje="El stock no puede ser negativo.")

    db = admin()

    if not _verificar_ownership(db, inventario_id, vivero_id):
        return AccionResultado(ok=False, mensaje="Este item no pertenece a tu vivero.")

    nombre = params.get("nombre") or _nombre_planta(db, inventario_id)

    # Si stock es 0, marcar como agotado automáticamente
    update_payload: dict = {"stock": nuevo_stock}
    if nuevo_stock == 0:
        update_payload["estado_planta"] = "agotado"

    resp = db.table("inventario").update(update_payload).eq(
        "inventario_id", inventario_id
    ).execute()

    if not resp.data:
        return AccionResultado(ok=False, mensaje="No se pudo actualizar el stock.")

    estado_msg = " — marcada como *agotada* automáticamente." if nuevo_stock == 0 else ""
    return AccionResultado(
        ok=True,
        mensaje=(
            f"✅ *{nombre}* — stock actualizado a *{nuevo_stock} unidades*{estado_msg}"
        ),
        datos={"inventario_id": inventario_id, "nuevo_stock": nuevo_stock}
    )


def _actualizar_estado(params: dict, vivero_id: int) -> AccionResultado:
    """Actualiza el estado de un item del inventario."""
    inventario_id = params.get("inventario_id")
    nuevo_estado = params.get("nuevo_estado")

    ESTADOS_VALIDOS = ("disponible", "agotado", "reservado", "en_crecimiento")

    if not inventario_id or not nuevo_estado:
        return AccionResultado(ok=False, mensaje="Faltan datos para actualizar el estado.")

    if nuevo_estado not in ESTADOS_VALIDOS:
        return AccionResultado(
            ok=False,
            mensaje=f"Estado inválido. Opciones: {', '.join(ESTADOS_VALIDOS)}"
        )

    db = admin()

    if not _verificar_ownership(db, inventario_id, vivero_id):
        return AccionResultado(ok=False, mensaje="Este item no pertenece a tu vivero.")

    nombre = params.get("nombre") or _nombre_planta(db, inventario_id)

    resp = db.table("inventario").update({
        "estado_planta": nuevo_estado,
    }).eq("inventario_id", inventario_id).execute()

    if not resp.data:
        return AccionResultado(ok=False, mensaje="No se pudo actualizar el estado.")

    estados_emoji = {
        "disponible": "🟢",
        "agotado": "🔴",
        "reservado": "🟡",
        "en_crecimiento": "🌱",
    }
    emoji = estados_emoji.get(nuevo_estado, "")

    return AccionResultado(
        ok=True,
        mensaje=f"✅ *{nombre}* — estado cambiado a {emoji} *{nuevo_estado}*",
        datos={"inventario_id": inventario_id, "nuevo_estado": nuevo_estado}
    )


def _agregar_producto(params: dict, vivero_id: int) -> AccionResultado:
    """Agrega una nueva planta al inventario del viverista."""
    nombre_comun = params.get("nombre_comun")
    nombre_cientifico = params.get("nombre_cientifico")
    precio_mayorista = params.get("precio_mayorista", 0)
    stock = params.get("stock", 1)
    altura_cm = params.get("altura_cm", 30)
    foto_url = params.get("foto_url")
    confianza_yolo = params.get("confianza_yolo")

    if not nombre_comun:
        return AccionResultado(ok=False, mensaje="Se necesita el nombre de la planta.")

    db = admin()

    # Buscar o crear planta en catálogo base
    planta_id = params.get("planta_id")
    if not planta_id:
        # Buscar por nombre científico si existe
        if nombre_cientifico:
            existing = db.table("plantas").select("planta_id").eq(
                "nombre_cientifico", nombre_cientifico
            ).limit(1).execute()
            if existing.data:
                planta_id = existing.data[0]["planta_id"]

        if not planta_id:
            planta_resp = db.table("plantas").insert({
                "nombre_comun": nombre_comun,
                "nombre_cientifico": nombre_cientifico,
                "activa": True,
            }).execute()
            if not planta_resp.data:
                return AccionResultado(ok=False, mensaje="No se pudo crear la planta.")
            planta_id = planta_resp.data[0]["planta_id"]

    # Insertar en inventario
    try:
        inv_resp = db.table("inventario").insert({
            "vivero_id": vivero_id,
            "planta_id": planta_id,
            "altura_cm": altura_cm,
            "precio_mayorista": precio_mayorista,
            "stock": stock,
            "unidad_medida": "unidad",
            "foto_ia_url": foto_url,
            "confianza_yolo": confianza_yolo,
            "estado_planta": "disponible",
            "origen_carga": "whatsapp_ia",
        }).execute()
    except Exception as e:
        msg = str(e).lower()
        if "23505" in msg or "duplicate" in msg or "unique" in msg:
            return AccionResultado(
                ok=False,
                mensaje=(
                    f"Ya tenés *{nombre_comun}* de {altura_cm}cm en tu inventario. "
                    f"Si querés actualizar precio o stock, decime cuál cambiar."
                )
            )
        raise

    precio_comprador = round(precio_mayorista * 1.18)
    return AccionResultado(
        ok=True,
        mensaje=(
            f"✅ *{nombre_comun}* agregada a tu catálogo 🌿\n"
            f"• Stock: {stock} unidades\n"
            f"• Tu precio: ${precio_mayorista:,.0f} COP\n"
            f"• Comprador paga: ${precio_comprador:,.0f} COP\n"
            f"Ya está visible en el marketplace."
        ),
        datos={
            "inventario_id": inv_resp.data[0]["inventario_id"],
            "planta_id": planta_id,
        }
    )


def _aprobar_pedido(params: dict, vivero_id: int) -> AccionResultado:
    """Aprueba una cotización pendiente."""
    cotizacion_id = params.get("cotizacion_id")
    if not cotizacion_id:
        return AccionResultado(ok=False, mensaje="No se encontró el pedido a aprobar.")

    db = admin()

    # Verificar que la cotización existe y está en estado enviada
    cot = db.table("cotizaciones").select(
        "cotizacion_id, estado, items, total_estimado, prompt_original, cliente_id"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

    if not cot.data:
        return AccionResultado(ok=False, mensaje="Pedido no encontrado.")

    cotizacion = cot.data[0]
    if cotizacion["estado"] != "enviada":
        return AccionResultado(
            ok=False,
            mensaje=f"Este pedido ya fue procesado (estado: {cotizacion['estado']})."
        )

    # Verificar que tiene items del vivero
    items = cotizacion.get("items") or []
    inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
    if inv_ids:
        inv_resp = db.table("inventario").select("inventario_id, vivero_id").in_(
            "inventario_id", inv_ids
        ).execute()
        es_del_vivero = any(
            r.get("vivero_id") == vivero_id for r in (inv_resp.data or [])
        )
        if not es_del_vivero:
            return AccionResultado(
                ok=False,
                mensaje="Este pedido no contiene plantas de tu vivero."
            )

    # Aprobar
    db.table("cotizaciones").update({
        "estado": "aceptada"
    }).eq("cotizacion_id", cotizacion_id).execute()

    # Notificar al comprador
    try:
        from app.config import get_settings
        from app.services.whatsapp_meta import send_text_message
        import asyncio

        cliente = db.table("clientes").select("whatsapp_numero").eq(
            "cliente_id", cotizacion["cliente_id"]
        ).limit(1).execute()

        if cliente.data and cliente.data[0].get("whatsapp_numero"):
            base = get_settings().app_base_url
            total_base = int(float(cotizacion.get("total_estimado") or 0))
            total_comprador = round(total_base * 1.18)
            nombre_proyecto = cotizacion.get("prompt_original") or f"Pedido #{cotizacion_id}"
            msg = (
                f"✅ *¡Tu cotización fue aprobada! — ViveroOnline*\n\n"
                f"Proyecto: {nombre_proyecto}\n"
                f"Total a pagar: ${total_comprador:,} COP\n\n"
                f"Completá el pago aquí:\n"
                f"{base}/comprador"
            )
            asyncio.create_task(
                send_text_message(cliente.data[0]["whatsapp_numero"], msg)
            )
    except Exception:
        pass  # No bloqueamos si falla la notificación

    nombre_proyecto = cotizacion.get("prompt_original") or f"Pedido #{cotizacion_id}"
    total = int(float(cotizacion.get("total_estimado") or 0))

    return AccionResultado(
        ok=True,
        mensaje=(
            f"✅ *Pedido aprobado*\n"
            f"Proyecto: {nombre_proyecto}\n"
            f"Monto: ${total:,} COP\n\n"
            f"El comprador fue notificado y ya puede proceder con el pago. 🌿"
        ),
        datos={"cotizacion_id": cotizacion_id}
    )


def _rechazar_pedido(params: dict, vivero_id: int) -> AccionResultado:
    """Rechaza una cotización pendiente."""
    cotizacion_id = params.get("cotizacion_id")
    motivo = params.get("motivo", "")

    if not cotizacion_id:
        return AccionResultado(ok=False, mensaje="No se encontró el pedido a rechazar.")

    db = admin()

    cot = db.table("cotizaciones").select(
        "cotizacion_id, estado, prompt_original, cliente_id"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

    if not cot.data:
        return AccionResultado(ok=False, mensaje="Pedido no encontrado.")

    if cot.data[0]["estado"] != "enviada":
        return AccionResultado(
            ok=False,
            mensaje=f"Este pedido ya fue procesado (estado: {cot.data[0]['estado']})."
        )

    db.table("cotizaciones").update({
        "estado": "rechazada",
        "notas_agente": motivo or "Rechazada por el viverista vía WhatsApp",
    }).eq("cotizacion_id", cotizacion_id).execute()

    # Notificar al comprador
    try:
        from app.config import get_settings
        from app.services.whatsapp_meta import send_text_message
        import asyncio

        cliente = db.table("clientes").select("whatsapp_numero").eq(
            "cliente_id", cot.data[0]["cliente_id"]
        ).limit(1).execute()

        if cliente.data and cliente.data[0].get("whatsapp_numero"):
            base = get_settings().app_base_url
            nombre_proyecto = cot.data[0].get("prompt_original") or f"Pedido #{cotizacion_id}"
            motivo_txt = f"\nMotivo: {motivo}" if motivo else ""
            msg = (
                f"❌ *Cotización no disponible — ViveroOnline*\n\n"
                f"Proyecto: {nombre_proyecto}{motivo_txt}\n\n"
                f"Podés buscar alternativas en el marketplace:\n"
                f"{base}/marketplace"
            )
            asyncio.create_task(
                send_text_message(cliente.data[0]["whatsapp_numero"], msg)
            )
    except Exception:
        pass

    nombre_proyecto = cot.data[0].get("prompt_original") or f"Pedido #{cotizacion_id}"

    return AccionResultado(
        ok=True,
        mensaje=(
            f"❌ *Pedido rechazado*\n"
            f"Proyecto: {nombre_proyecto}\n"
            f"El comprador fue notificado."
        ),
        datos={"cotizacion_id": cotizacion_id}
    )
