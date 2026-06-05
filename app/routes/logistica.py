"""Módulo de logística — ViveroOnline MVP.

Modelo: Vivero ejecuta, ViveroOnline audita.
Se activa automáticamente cuando se confirma un pago.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


async def generar_entregas_y_notificar(cotizacion_id: int) -> dict:
    """
    Post-pago: genera registros en la tabla entregas y
    notifica a cada viverista involucrado por WhatsApp.

    Devuelve {"ok": True, "entregas_creadas": N}
    """
    from app.services.supabase import admin as db_admin
    db = db_admin()

    # 1. Leer cotización con datos de entrega
    cot_resp = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, items, total_estimado, "
        "ciudad_entrega, direccion_entrega_exacta, "
        "contacto_nombre, contacto_telefono, "
        "tipo_vehiculo, ventana_inicio, ventana_fin, notas_cliente"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

    if not cot_resp.data:
        logger.error(f"Cotización {cotizacion_id} no encontrada para generar entregas")
        return {"ok": False, "error": "Cotización no encontrada"}

    cot = cot_resp.data[0]
    items = cot.get("items") or []

    if not cot.get("direccion_entrega_exacta"):
        logger.warning(f"Cotización {cotizacion_id} sin dirección de entrega — no se generan entregas")
        return {"ok": False, "error": "Sin dirección de entrega"}

    # 2. Llamar función SQL que agrupa por vivero
    try:
        entrega_resp = db.rpc("generar_entregas_post_pago", {
            "p_cotizacion_id": cotizacion_id,
            "p_direccion": cot.get("direccion_entrega_exacta", ""),
            "p_contacto_nombre": cot.get("contacto_nombre", ""),
            "p_contacto_telefono": cot.get("contacto_telefono", ""),
            "p_tipo_vehiculo": cot.get("tipo_vehiculo", "camioneta"),
            "p_ventana_inicio": cot.get("ventana_inicio"),
            "p_ventana_fin": cot.get("ventana_fin"),
        }).execute()
        entregas = entrega_resp.data or []
    except Exception as e:
        logger.exception(f"Error generando entregas para cotización {cotizacion_id}: {e}")
        entregas = []

    # 3. Notificar a cada viverista por WhatsApp
    for entrega in entregas:
        vivero_id = entrega.get("vivero_id")
        if not vivero_id:
            continue
        await _notificar_viverista(db, entrega, cot)

    logger.info(f"Cotización {cotizacion_id}: {len(entregas)} entrega(s) generada(s)")
    return {"ok": True, "entregas_creadas": len(entregas)}


async def _notificar_viverista(db, entrega: dict, cot: dict):
    """Envía WhatsApp al viverista con los datos de entrega."""
    try:
        vivero_id = entrega.get("vivero_id")
        vivero_resp = db.table("viveros").select(
            "nombre_vivero, whatsapp_numero"
        ).eq("vivero_id", vivero_id).limit(1).execute()

        if not vivero_resp.data:
            return
        vivero = vivero_resp.data[0]
        wa_numero = vivero.get("whatsapp_numero")
        if not wa_numero:
            logger.warning(f"Vivero {vivero_id} sin WhatsApp — no se puede notificar")
            return

        # Construir mensaje
        nombre_vivero = vivero.get("nombre_vivero", "Vivero")
        items_vivero = entrega.get("items_vivero") or []
        resumen_items = _resumir_items(items_vivero)
        tipo_vehiculo = entrega.get("tipo_vehiculo", "camioneta")
        ventana = _formatear_ventana(
            entrega.get("ventana_inicio"),
            entrega.get("ventana_fin")
        )
        notas = cot.get("notas_cliente", "")
        base_url = os.environ.get("APP_BASE_URL", "https://app.viveroonline.com.co")

        mensaje = (
            f"🌿 *Nuevo pedido pagado — ViveroOnline*\n\n"
            f"Hola {nombre_vivero}, tenés un pedido confirmado:\n\n"
            f"📦 *Plantas:*\n{resumen_items}\n\n"
            f"📍 *Dirección de entrega:*\n{cot.get('direccion_entrega_exacta', '')}\n"
            f"{cot.get('ciudad_entrega', '')}\n\n"
            f"👤 *Contacto en obra:*\n"
            f"{cot.get('contacto_nombre', '')} · {cot.get('contacto_telefono', '')}\n\n"
            f"🚐 *Vehículo necesario:* {tipo_vehiculo.title()}\n"
            f"🕐 *Horario de recepción:* {ventana}\n"
        )
        if notas:
            mensaje += f"\n📝 *Notas:* {notas}\n"

        mensaje += (
            f"\n*¿Cuándo podés despacharlo?*\n"
            f"Cuando lo enviés, subí una foto como evidencia y "
            f"escribí 'Despachado' acá.\n\n"
            f"👉 Ver detalle: {base_url}/viverista"
        )

        from app.services.whatsapp_meta import send_text_message
        await send_text_message(wa_numero, mensaje)
        logger.info(f"WhatsApp enviado a vivero {vivero_id} ({wa_numero})")

    except Exception as e:
        logger.exception(f"Error notificando viverista {entrega.get('vivero_id')}: {e}")


def _resumir_items(items: list) -> str:
    if not items:
        return "- (sin detalle)"
    lineas = []
    for item in items[:8]:  # máximo 8 líneas
        nombre = item.get("nombre_comun") or item.get("nombre") or "Planta"
        cantidad = item.get("cantidad", 1)
        lineas.append(f"  • {cantidad} × {nombre}")
    if len(items) > 8:
        lineas.append(f"  ... y {len(items) - 8} más")
    return "\n".join(lineas)


def _formatear_ventana(inicio: Optional[str], fin: Optional[str]) -> str:
    if not inicio:
        return "A coordinar con el comprador"
    try:
        ini = datetime.fromisoformat(str(inicio).replace("Z", "+00:00"))
        texto = ini.strftime("%d/%m/%Y %H:%M")
        if fin:
            f = datetime.fromisoformat(str(fin).replace("Z", "+00:00"))
            texto += f" – {f.strftime('%H:%M')}"
        return texto
    except Exception:
        return str(inicio)


async def actualizar_estado_entrega(
    cotizacion_id: int,
    nuevo_estado: str,
    evidencia_url: Optional[str] = None,
    notas: Optional[str] = None,
) -> dict:
    """Actualiza el estado de todas las entregas de una cotización."""
    from app.services.supabase import admin as db_admin
    db = db_admin()

    update = {"estado_entrega": nuevo_estado}
    if nuevo_estado == "despachado":
        update["fecha_despacho"] = datetime.utcnow().isoformat()
    if nuevo_estado == "entregado":
        update["fecha_entrega"] = datetime.utcnow().isoformat()
    if evidencia_url:
        update["evidencia_url"] = evidencia_url
    if notas:
        update["notas_vivero"] = notas

    resp = db.table("entregas").update(update).eq(
        "cotizacion_id", cotizacion_id
    ).execute()
    return {"ok": True, "actualizadas": len(resp.data or [])}
