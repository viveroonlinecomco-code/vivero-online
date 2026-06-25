"""Onboarding de viveristas — flujo de 4 hitos por WhatsApp.

Este módulo gestiona el flujo automatizado de bienvenida y activación
para viveristas nuevos durante sus primeros 7 días post-aceptación
del Contrato de Mandato Comercial.

ARQUITECTURA (2026-06-24):
- Gate de seguridad: el flujo SOLO arranca cuando se acepta el mandato.
  Esto está garantizado a nivel SQL (trigger trg_crear_onboarding crea
  la fila de onboarding_viverista_hitos automáticamente).
- Día 0 (bienvenida + pedir foto) → invocado desde datos_fiscales_wa.py
  al confirmar aceptación del mandato. NO viene del cron.
- Días 1, 3 y 7 → invocados por cron diario (Edge Function
  cron-onboarding-viveristas, que llama un endpoint del backend).
- Cada envío revalida estado del vivero y aceptación de mandato antes
  de enviar.

GATES DE SEGURIDAD RUNTIME:
1. vivero.estado != 'inactivo'
2. datos_fiscales_vivero.mandato_aceptado = TRUE
3. mandato_whatsapp_numero no nulo
4. hito_X_enviado_at IS NULL (idempotencia)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .supabase import admin
from .whatsapp_meta import send_text_message

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# TEMPLATES DE MENSAJES
# ═══════════════════════════════════════════════════════════════════

TPL_HITO_0 = (
    "🌱 ¡Bienvenido a ViveroOnline, {nombre_vivero}!\n\n"
    "Ya quedaste registrado como vivero aliado. Para empezar a vender, "
    "mandame una foto de una planta que tengas en stock. "
    "Yo identifico la especie y la cargo al catálogo.\n\n"
    "¡Vamos!"
)

TPL_HITO_1_SIN_PRODUCTO = (
    "🌿 ¡Hola de nuevo, {nombre_vivero}!\n\n"
    "Vi que aún no cargaste tu primera planta. No te preocupes, "
    "es súper fácil — solo mandame una foto y yo me encargo del resto.\n\n"
    "¿Probamos?"
)

TPL_HITO_1_CON_PRODUCTO = (
    "🎉 ¡Excelente, {nombre_vivero}!\n\n"
    "Ya tenés {n_productos} producto(s) publicado(s). "
    "Ahora confirmá el precio mayorista respondiendo:\n"
    "precio [ID] [valor]\n\n"
    "Si necesitás ayuda, escribime."
)

TPL_HITO_3 = (
    "🌳 Llevás 3 días con nosotros, {nombre_vivero}.\n\n"
    "¿Sabías que paisajistas y constructoras de la Sabana están "
    "buscando proveedores como vos?\n\n"
    "📚 Guía para arrancar bien: https://www.viveroonline.com.co\n\n"
    "Tip del día: cargá al menos 5 productos para aumentar "
    "tus chances de aparecer en cotizaciones."
)

TPL_HITO_7_CON_COTIZACION = (
    "🎉 ¡Felicitaciones, {nombre_vivero}!\n\n"
    "Tu primera cotización ya llegó. Para aprobarla respondé:\n"
    "APROBAR [ID]\n\n"
    "Si necesitás rechazarla:\n"
    "RECHAZAR [ID] [motivo]"
)

TPL_HITO_7_SIN_COTIZACION = (
    "🌱 Una semana con nosotros, {nombre_vivero}.\n\n"
    "Los viveros que cargan 10+ productos reciben en promedio su "
    "primera cotización en los primeros 14 días.\n\n"
    "Si necesitás ayuda para cargar más productos rápido, escribime.\n\n"
    "¡Vamos por esa primera venta!"
)


# ═══════════════════════════════════════════════════════════════════
# HELPERS DE CONTEXTO
# ═══════════════════════════════════════════════════════════════════

def _obtener_contexto_vivero(vivero_id: int) -> Optional[dict]:
    try:
        resp = (
            admin().table("viveros")
            .select(
                "vivero_id, nombre_vivero, estado, "
                "datos_fiscales_vivero(mandato_aceptado, mandato_whatsapp_numero)"
            )
            .eq("vivero_id", vivero_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            logger.warning(f"onboarding: vivero {vivero_id} no encontrado")
            return None

        v = resp.data[0]
        if v.get("estado") == "inactivo":
            return None

        df_list = v.get("datos_fiscales_vivero") or []
        df = df_list[0] if df_list else {}

        if not df.get("mandato_aceptado"):
            return None

        wa_num = df.get("mandato_whatsapp_numero")
        if not wa_num:
            return None

        return {
            "nombre_vivero": v.get("nombre_vivero") or "viverista",
            "whatsapp_numero": wa_num,
            "estado": v.get("estado"),
        }
    except Exception as e:
        logger.error(f"onboarding: error obteniendo contexto vivero {vivero_id}: {e}")
        return None


def _contar_productos(vivero_id: int) -> int:
    try:
        resp = (
            admin().table("inventario")
            .select("inventario_id", count="exact")
            .eq("vivero_id", vivero_id)
            .execute()
        )
        return resp.count or 0
    except Exception:
        return 0


def _tuvo_primera_cotizacion(vivero_id: int) -> bool:
    try:
        resp = (
            admin().table("onboarding_viverista_hitos")
            .select("primera_cotizacion_at")
            .eq("vivero_id", vivero_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return False
        return resp.data[0].get("primera_cotizacion_at") is not None
    except Exception:
        return False


def _parse_fecha(valor) -> Optional[datetime]:
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    try:
        s = str(valor).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
# FUNCIONES PÚBLICAS
# ═══════════════════════════════════════════════════════════════════

async def iniciar_hito_0(vivero_id: int) -> dict:
    return await _enviar_hito(vivero_id, hito_num=0)


async def procesar_hito_diario(onboarding_row: dict) -> dict:
    try:
        vivero_id = onboarding_row["vivero_id"]
        fecha_dia_0 = _parse_fecha(onboarding_row.get("fecha_dia_0"))
        if not fecha_dia_0:
            return {"accion": "error", "error": "fecha_dia_0 inválida"}

        dias = (datetime.now(timezone.utc) - fecha_dia_0).days

        if dias >= 7 and not onboarding_row.get("hito_7_enviado_at"):
            return await _enviar_hito(vivero_id, hito_num=7)
        if dias >= 3 and not onboarding_row.get("hito_3_enviado_at"):
            return await _enviar_hito(vivero_id, hito_num=3)
        if dias >= 1 and not onboarding_row.get("hito_1_enviado_at"):
            return await _enviar_hito(vivero_id, hito_num=1)

        return {"accion": "saltado", "razon": "sin_hito_pendiente_aun"}
    except Exception as e:
        logger.error(f"procesar_hito_diario falló: {e}", exc_info=True)
        return {"accion": "error", "error": str(e)}


def marcar_primer_producto(vivero_id: int) -> bool:
    return _marcar_evento(vivero_id, "primer_producto_at")


def marcar_primera_cotizacion(vivero_id: int) -> bool:
    return _marcar_evento(vivero_id, "primera_cotizacion_at")


# ═══════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════

async def _enviar_hito(vivero_id: int, hito_num: int) -> dict:
    ctx = _obtener_contexto_vivero(vivero_id)
    if not ctx:
        return {"accion": "saltado", "hito": hito_num, "razon": "gate_seguridad_no_pasa"}

    if hito_num == 0:
        mensaje = TPL_HITO_0.format(nombre_vivero=ctx["nombre_vivero"])
    elif hito_num == 1:
        n_productos = _contar_productos(vivero_id)
        if n_productos > 0:
            mensaje = TPL_HITO_1_CON_PRODUCTO.format(
                nombre_vivero=ctx["nombre_vivero"], n_productos=n_productos
            )
        else:
            mensaje = TPL_HITO_1_SIN_PRODUCTO.format(nombre_vivero=ctx["nombre_vivero"])
    elif hito_num == 3:
        mensaje = TPL_HITO_3.format(nombre_vivero=ctx["nombre_vivero"])
    elif hito_num == 7:
        if _tuvo_primera_cotizacion(vivero_id):
            mensaje = TPL_HITO_7_CON_COTIZACION.format(nombre_vivero=ctx["nombre_vivero"])
        else:
            mensaje = TPL_HITO_7_SIN_COTIZACION.format(nombre_vivero=ctx["nombre_vivero"])
    else:
        return {"accion": "error", "error": f"hito_num inválido: {hito_num}"}

    ok = await send_text_message(ctx["whatsapp_numero"], mensaje)
    if not ok:
        return {"accion": "error", "hito": hito_num, "error": "send_text_message devolvió False"}

    return _marcar_hito_enviado(vivero_id, hito_num)


def _marcar_hito_enviado(vivero_id: int, hito_num: int) -> dict:
    try:
        campo = f"hito_{hito_num}_enviado_at"
        now_iso = datetime.now(timezone.utc).isoformat()
        update = {campo: now_iso, "fecha_actualizacion": now_iso}
        if hito_num == 7:
            update["completado"] = True
            update["fecha_completado"] = now_iso

        admin().table("onboarding_viverista_hitos").update(update).eq(
            "vivero_id", vivero_id
        ).execute()

        return {"accion": "enviado", "hito": hito_num}
    except Exception as e:
        logger.error(f"onboarding: fallo marcando hito {hito_num} vivero {vivero_id}: {e}")
        return {"accion": "error", "hito": hito_num, "error": str(e)}


def _marcar_evento(vivero_id: int, campo: str) -> bool:
    try:
        resp = (
            admin().table("onboarding_viverista_hitos")
            .select(campo)
            .eq("vivero_id", vivero_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return False
        if resp.data[0].get(campo):
            return False

        now_iso = datetime.now(timezone.utc).isoformat()
        admin().table("onboarding_viverista_hitos").update(
            {campo: now_iso, "fecha_actualizacion": now_iso}
        ).eq("vivero_id", vivero_id).execute()
        return True
    except Exception as e:
        logger.error(f"onboarding: fallo marcando {campo} vivero {vivero_id}: {e}")
        return False
