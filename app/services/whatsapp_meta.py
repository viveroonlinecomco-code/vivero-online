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
  de enviar. Si el viverista revoca el mandato o se suspende el vivero,
  el cron deja de enviarle mensajes sin necesidad de acción manual.

GATES DE SEGURIDAD RUNTIME:
1. vivero.estado != 'inactivo'
2. datos_fiscales_vivero.mandato_aceptado = TRUE
3. mandato_whatsapp_numero no nulo
4. hito_X_enviado_at IS NULL (idempotencia)

MENSAJES PERSONALIZADOS:
- Día 1: condicional según si ya cargó producto o no
- Día 7: condicional según si ya recibió cotización o no

CONVENCIONES DEL PROYECTO:
- Supabase: usar admin() en cada llamada (NO cachear el Client; ver
  docstring en app/services/supabase.py).
- WhatsApp: send_text_message es async y retorna bool (False en error,
  no raise). Por eso las funciones que envían son async; las que solo
  tocan Supabase quedan sync.

Para editar mensajes: modificar las constantes TPL_HITO_* abajo.
Cero Gemini en este módulo — todos los textos son hardcodeados.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .supabase import admin
from .whatsapp_meta import send_text_message

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# TEMPLATES DE MENSAJES (editables sin tocar lógica)
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
# HELPERS DE CONTEXTO (sync — solo tocan Supabase)
# ═══════════════════════════════════════════════════════════════════

def _obtener_contexto_vivero(vivero_id: int) -> Optional[dict]:
    """Trae datos del vivero + WhatsApp para envío.

    Aplica gates de seguridad:
    - vivero existe
    - vivero.estado != 'inactivo'
    - datos_fiscales_vivero.mandato_aceptado = TRUE
    - mandato_whatsapp_numero no nulo

    Returns:
        dict con {nombre_vivero, whatsapp_numero, estado} si pasa todos los
        gates, o None si falla alguno (en cuyo caso NO se envía mensaje).
    """
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
            logger.info(f"onboarding: vivero {vivero_id} inactivo, skip")
            return None

        # datos_fiscales_vivero viene como lista (relación 1:1 vía supabase-py)
        df_list = v.get("datos_fiscales_vivero") or []
        df = df_list[0] if df_list else {}

        if not df.get("mandato_aceptado"):
            logger.info(
                f"onboarding: vivero {vivero_id} sin mandato aceptado, skip"
            )
            return None

        wa_num = df.get("mandato_whatsapp_numero")
        if not wa_num:
            logger.warning(
                f"onboarding: vivero {vivero_id} sin whatsapp_numero, skip"
            )
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
    """Cuenta items en inventario del vivero. Cero si error."""
    try:
        resp = (
            admin().table("inventario")
            .select("inventario_id", count="exact")
            .eq("vivero_id", vivero_id)
            .execute()
        )
        return resp.count or 0
    except Exception as e:
        logger.error(f"onboarding: error contando productos {vivero_id}: {e}")
        return 0


def _tuvo_primera_cotizacion(vivero_id: int) -> bool:
    """Verifica el campo primera_cotizacion_at en onboarding."""
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
    """Parsea timestamp ISO de Supabase a datetime tz-aware."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    try:
        # Supabase devuelve formato "2026-06-24T15:23:53.862856+00:00"
        s = str(valor).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
# FUNCIONES PÚBLICAS — async porque envían WhatsApp
# ═══════════════════════════════════════════════════════════════════

async def iniciar_hito_0(vivero_id: int) -> dict:
    """Envía mensaje de bienvenida (Día 0).

    Llamado desde datos_fiscales_wa.py inmediatamente después de
    confirmar la aceptación del mandato. La fila de onboarding YA fue
    creada por el trigger SQL; acá solo enviamos el mensaje y marcamos
    hito_0_enviado_at.

    Args:
        vivero_id: ID del viverista que acaba de firmar.

    Returns:
        dict con {accion, hito, error?}.
    """
    return await _enviar_hito(vivero_id, hito_num=0)


async def procesar_hito_diario(onboarding_row: dict) -> dict:
    """Procesa una fila de onboarding. Decide qué hito enviar según día.

    Invocado por el cron diario (vía endpoint backend) para cada fila
    incompleta de onboarding_viverista_hitos.

    Args:
        onboarding_row: dict con campos de la tabla:
            vivero_id, fecha_dia_0, hito_1_enviado_at, hito_3_enviado_at,
            hito_7_enviado_at.

    Returns:
        dict con {accion, hito, error}. Acción puede ser:
        - "enviado": se envió un hito (cuál en `hito`)
        - "saltado": no había hito pendiente para hoy, o validación falló
        - "error": ocurrió excepción (detalle en `error`)
    """
    try:
        vivero_id = onboarding_row["vivero_id"]
        fecha_dia_0 = _parse_fecha(onboarding_row.get("fecha_dia_0"))
        if not fecha_dia_0:
            return {"accion": "error", "error": "fecha_dia_0 inválida"}

        dias = (datetime.now(timezone.utc) - fecha_dia_0).days

        # Prioridad: hito más alto primero. Si el cron se perdió un día,
        # no enviamos hito 3 cuando ya correspondería el 7.
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
    """Marca primer_producto_at si aún no está marcado.

    Llamado desde whatsapp.py al cargar primer producto del viverista
    (típicamente al procesar primera foto enviada). Idempotente: si ya
    está marcado, no hace nada.

    SYNC porque solo toca Supabase; puede llamarse desde código sync o async.

    Returns:
        True si lo marcó ahora, False si ya estaba marcado o falló.
    """
    return _marcar_evento(vivero_id, "primer_producto_at")


def marcar_primera_cotizacion(vivero_id: int) -> bool:
    """Marca primera_cotizacion_at si aún no está marcado.

    Llamado desde pedidos.py al crear primera cotización para este
    vivero. Idempotente.

    SYNC porque solo toca Supabase; puede llamarse desde código sync o async.

    Returns:
        True si lo marcó ahora, False si ya estaba marcado o falló.
    """
    return _marcar_evento(vivero_id, "primera_cotizacion_at")


# ═══════════════════════════════════════════════════════════════════
# INTERNAL — envío (async) y marcado (sync)
# ═══════════════════════════════════════════════════════════════════

async def _enviar_hito(vivero_id: int, hito_num: int) -> dict:
    """Envía el mensaje del hito y marca hito_X_enviado_at.

    ASYNC porque send_text_message es async.
    """
    ctx = _obtener_contexto_vivero(vivero_id)
    if not ctx:
        return {
            "accion": "saltado",
            "hito": hito_num,
            "razon": "gate_seguridad_no_pasa",
        }

    # Armar el mensaje según hito + condiciones
    if hito_num == 0:
        mensaje = TPL_HITO_0.format(nombre_vivero=ctx["nombre_vivero"])
    elif hito_num == 1:
        n_productos = _contar_productos(vivero_id)
        if n_productos > 0:
            mensaje = TPL_HITO_1_CON_PRODUCTO.format(
                nombre_vivero=ctx["nombre_vivero"], n_productos=n_productos
            )
        else:
            mensaje = TPL_HITO_1_SIN_PRODUCTO.format(
                nombre_vivero=ctx["nombre_vivero"]
            )
    elif hito_num == 3:
        mensaje = TPL_HITO_3.format(nombre_vivero=ctx["nombre_vivero"])
    elif hito_num == 7:
        if _tuvo_primera_cotizacion(vivero_id):
            mensaje = TPL_HITO_7_CON_COTIZACION.format(
                nombre_vivero=ctx["nombre_vivero"]
            )
        else:
            mensaje = TPL_HITO_7_SIN_COTIZACION.format(
                nombre_vivero=ctx["nombre_vivero"]
            )
    else:
        return {"accion": "error", "error": f"hito_num inválido: {hito_num}"}

    # Enviar WhatsApp (async, retorna bool — no raise)
    ok = await send_text_message(ctx["whatsapp_numero"], mensaje)
    if not ok:
        logger.error(
            f"onboarding: fallo envío WA vivero {vivero_id} hito {hito_num}"
        )
        return {
            "accion": "error",
            "hito": hito_num,
            "error": "send_text_message devolvió False",
        }

    # Marcar como enviado solo si el envío fue exitoso
    return _marcar_hito_enviado(vivero_id, hito_num)


def _marcar_hito_enviado(vivero_id: int, hito_num: int) -> dict:
    """Actualiza hito_X_enviado_at. Si es hito 7, marca completado."""
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
        logger.error(
            f"onboarding: fallo marcando hito {hito_num} vivero {vivero_id}: {e}"
        )
        return {"accion": "error", "hito": hito_num, "error": str(e)}


def _marcar_evento(vivero_id: int, campo: str) -> bool:
    """Marca primer_producto_at o primera_cotizacion_at solo si es NULL."""
    try:
        # Verificar que aún no esté marcado
        resp = (
            admin().table("onboarding_viverista_hitos")
            .select(campo)
            .eq("vivero_id", vivero_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return False  # vivero sin onboarding (no firmó mandato)
        if resp.data[0].get(campo):
            return False  # ya estaba marcado

        now_iso = datetime.now(timezone.utc).isoformat()
        admin().table("onboarding_viverista_hitos").update(
            {campo: now_iso, "fecha_actualizacion": now_iso}
        ).eq("vivero_id", vivero_id).execute()
        return True
    except Exception as e:
        logger.error(f"onboarding: fallo marcando {campo} vivero {vivero_id}: {e}")
        return False
