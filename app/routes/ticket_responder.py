"""Sistema auto-respuesta tickets — Opción B HYBRID
Responde automático (COMPRA) + Derivar (RECLAMO/VENTA/B2B/VAGA)
Elena recibe: solicitud + respuesta + acción"""

import logging
import re
from typing import Optional

from app.services.supabase import admin as db_admin
from app.services.whatsapp_meta import send_text_message

logger = logging.getLogger(__name__)

BASE_URL_COMPRA = "https://app.viveroonline.com.co/marketplace"
BASE_URL_EMPRESA = "https://app.viveroonline.com.co/quienes-somos"
BASE_URL_DASHBOARD_ADMIN = "https://app.viveroonline.com.co/admin"


def _normalizar_nombre_planta(texto: str) -> str:
    stopwords = {"el", "la", "de", "del", "a", "para", "por", "en", "con", "sin"}
    palabras = [w.strip().lower() for w in re.split(r"[\s,;./]", texto) if w.strip()]
    palabras = [w for w in palabras if w not in stopwords and len(w) > 2]
    return " ".join(palabras[:3]) if palabras else texto.lower()


def _detectar_tipo_derivacion(tipo: str, desc: str) -> tuple[str, str]:
    """Detecta qué tipo de derivación es. Retorna (tipo_derivacion, motivo)"""
    desc_lower = desc.lower()

    if tipo == "reclamo" or re.search(
        r"dañado|roto|muerto|llegó mal|no funciona|problema|error|falla|devuelvo|reembolso",
        desc_lower,
    ):
        return ("reclamo", "ATENCIÓN URGENTE requerida")

    if tipo == "venta" or re.search(
        r"soy viverista|vendo plantas|quiero vender|registr|tengo \d+ plantas",
        desc_lower,
    ):
        return ("venta", "Onboarding viverista requerido")

    if re.search(
        r"empresa|constructora|paisajista|facturación|arl|eps|servicios|proveedor|b2b|contrato|cotización",
        desc_lower,
    ):
        return ("b2b", "Seguimiento comercial requerido")

    return ("consulta_vaga", "Análisis y clarificación requerida")


async def responder_ticket_segun_tipo(ticket_id: int, ticket_data: dict) -> dict:
    """OPCIÓN B HYBRID: Responde automático pero MANTIENE estado='pendiente'"""
    db = db_admin()
    whatsapp = ticket_data.get("whatsapp_numero")
    tipo = ticket_data.get("tipo_solicitud", "").lower()
    desc = (ticket_data.get("descripcion") or "").lower()
    nombre = ticket_data.get("nombre") or "Cliente"

    respuesta = None
    accion = "derivar_admin"
    motivo = ""
    tipo_derivacion = ""

    # TIPO: COMPRA
    if tipo == "compra":
        match_precio = re.search(
            r"(?:precio|cuesta|valor|cuánto|valen?|costo).*?(?:de\s+)?([a-záéíóúñ\s]+?)(?:\?|$)",
            desc,
        )
        if match_precio:
            nombre_planta = _normalizar_nombre_planta(match_precio.group(1))
            plantas_resp = db.table("plantas").select(
                "nombre_comun, precio_base"
            ).ilike("nombre_comun", f"%{nombre_planta}%").limit(1).execute()

            if plantas_resp.data:
                planta = plantas_resp.data[0]
                precio = planta.get("precio_base", 0)
                respuesta = (
                    f"🌿 *{planta.get('nombre_comun')}*\n\n"
                    f"💰 Precio desde: ${int(precio):,} COP\n\n"
                    f"🎯 *Comprá HOY sin intermediarios*\n"
                    f"🛒 {BASE_URL_COMPRA}\n\n"
                    f"✅ Stock actualizado • Entrega a domicilio"
                )
                accion = "pendiente_confirmacion"
            else:
                respuesta = (
                    f"🌿 ¡Hola {nombre}!\n\n"
                    f"Buscamos '{nombre_planta}' en nuestro catálogo.\n\n"
                    f"🎯 *Explorá nuestras plantas HOY*\n"
                    f"🛒 {BASE_URL_COMPRA}\n\n"
                    f"📸 Comparte foto o descripción para presupuesto personalizado."
                )
                accion = "pendiente_confirmacion"

        elif re.search(r"despacho|envío|zona|costo.*envío", desc):
            respuesta = (
                f"🚐 *Entregas a la Sabana de Bogotá*\n\n"
                f"Bogotá, Chía, Cajicá, Cota, Tabio, Tenjo, Madrid, Zipaquirá\n\n"
                f"💰 Desde $35.000 COP\n"
                f"📦 Embalaje especial plantas\n\n"
                f"🎯 *Armá tu pedido*\n"
                f"🛒 {BASE_URL_COMPRA}"
            )
            accion = "pendiente_confirmacion"

        else:
            respuesta = (
                f"🌿 ¡Hola {nombre}!\n\n"
                f"Amplio catálogo plantas, materas, accesorios\n\n"
                f"🎯 *Visitá nuestro marketplace*\n"
                f"🛒 {BASE_URL_COMPRA}\n\n"
                f"✅ Precios sin intermediarios • Entrega ágil"
            )
            accion = "pendiente_confirmacion"

    # TIPO: CONSULTA
    elif tipo == "consulta":
        if re.search(r"quiénes\s+somos|sobre.*ustedes|empresa", desc):
            respuesta = (
                f"🌱 *Sobre ViveroOnline*\n\n"
                f"Marketplace conecta viveristas con compradores\n"
                f"Plantas directo del vivero, sin intermediarios.\n\n"
                f"📄 {BASE_URL_EMPRESA}\n\n"
                f"💚 Misión: Plantas accesibles para todos"
            )
            accion = "pendiente_confirmacion"

        elif re.search(r"servicio|proveedor|facturación|arl|eps|b2b|empresa", desc):
            respuesta = (
                f"🌿 *Consulta Comercial*\n\n"
                f"Gracias por tu interés. Tu solicitud es importante.\n\n"
                f"📞 Nuestro equipo comercial te contactará pronto.\n"
                f"sales@viveroonline.com.co"
            )
            accion = "derivar_admin"
            tipo_derivacion, motivo = _detectar_tipo_derivacion(tipo, desc)

        else:
            accion = "derivar_admin"
            tipo_derivacion, motivo = _detectar_tipo_derivacion(tipo, desc)

    # TIPO: RECLAMO, VENTA, OTRO
    elif tipo in ("reclamo", "venta", "otro"):
        if tipo == "reclamo":
            respuesta = (
                f"🔴 *Reclamo Recibido*\n\n"
                f"Lamentamos. Nuestro equipo te contactará pronto.\n\n"
                f"📞 sales@viveroonline.com.co"
            )
            tipo_derivacion = "reclamo"
            motivo = "ATENCIÓN URGENTE requerida"
            accion = "derivar_admin"

        elif tipo == "venta":
            respuesta = (
                f"🌿 *Interés en Vender*\n\n"
                f"¡Excelente! Estamos buscando viveristas asociados.\n\n"
                f"📞 sales@viveroonline.com.co\n"
                f"🌐 {BASE_URL_EMPRESA}"
            )
            tipo_derivacion = "venta"
            motivo = "Onboarding viverista requerido"
            accion = "derivar_admin"

        else:
            accion = "derivar_admin"
            tipo_derivacion, motivo = _detectar_tipo_derivacion(tipo, desc)

    # Enviar respuesta
    respondido = False
    if respuesta and whatsapp:
        try:
            await send_text_message(whatsapp, respuesta)
            respondido = True
            logger.info(f"✅ Respuesta ticket #{ticket_id}")
        except Exception as e:
            logger.error(f"❌ Error ticket #{ticket_id}: {e}")

    # Actualizar BD
    if accion == "pendiente_confirmacion" and respondido:
        db.table("tickets_soporte").update({
            "estado_interno": "respondido_por_bot",
            "atendido_por": "bot_autorespuesta",
            "notas_admin": "Bot respondió. Revisar y confirmar cierre.",
        }).eq("ticket_id", ticket_id).execute()

    elif accion == "derivar_admin":
        db.table("tickets_soporte").update({
            "estado_interno": "requiere_analisis",
            "tipo_derivacion": tipo_derivacion,
            "notas_admin": f"DERIVAR [{tipo_derivacion.upper()}]: {motivo}",
        }).eq("ticket_id", ticket_id).execute()

    return {
        "respondido": respondido,
        "auto_respuesta": respuesta,
        "accion": accion,
        "tipo_derivacion": tipo_derivacion,
        "motivo": motivo,
    }


async def notificar_admin_con_contexto(
    ticket_id: int,
    ticket_data: dict,
    respuesta_info: dict,
    admin_whatsapp: str,
) -> bool:
    """Elena recibe: SOLICITUD + RESPUESTA + ACCIÓN"""
    tipo = ticket_data.get("tipo_solicitud", "").upper()
    whatsapp_cliente = ticket_data.get("whatsapp_numero", "")
    descripcion_cliente = ticket_data.get("descripcion", "")
    respuesta_bot = respuesta_info.get("auto_respuesta")
    accion = respuesta_info.get("accion")
    nombre_cliente = ticket_data.get("nombre", "Cliente")
    tipo_derivacion = respuesta_info.get("tipo_derivacion", "")
    motivo = respuesta_info.get("motivo", "")

    # RESPUESTA AUTOMÁTICA
    if accion == "pendiente_confirmacion" and respuesta_bot:
        msg = (
            f"✅ *Ticket #{ticket_id} — Bot respondió*\n\n"
            f"👤 {nombre_cliente} ({whatsapp_cliente})\n"
            f"🏷️ {tipo}\n\n"
            f"────── SOLICITUD ──────\n"
            f"{descripcion_cliente[:150]}\n\n"
            f"────── RESPUESTA ──────\n"
            f"{respuesta_bot[:250]}\n\n"
            f"────────────────────────\n\n"
            f"🎯 ¿Está OK? Confirma:\n"
            f"{BASE_URL_DASHBOARD_ADMIN}/ticket/{ticket_id}"
        )

    # DERIVACIÓN
    else:
        emoji_map = {"reclamo": "🔴", "venta": "🌿", "b2b": "💼", "consulta_vaga": "❓"}
        emoji = emoji_map.get(tipo_derivacion, "⚠️")

        prioridad_map = {
            "reclamo": "🔴 URGENTE",
            "venta": "🟠 MEDIA",
            "b2b": "🟡 COMERCIAL",
            "consulta_vaga": "🟢 BAJA",
        }
        prioridad = prioridad_map.get(tipo_derivacion, "Normal")

        msg = (
            f"{emoji} *Ticket #{ticket_id} — {tipo_derivacion.upper()}*\n\n"
            f"👤 {nombre_cliente} ({whatsapp_cliente})\n"
            f"🏷️ {tipo}\n"
            f"📊 {prioridad}\n\n"
            f"────── SOLICITUD ──────\n"
            f"{descripcion_cliente[:150]}\n\n"
        )

        if respuesta_bot:
            msg += (
                f"────── RESPUESTA ──────\n"
                f"{respuesta_bot[:200]}\n\n"
            )

        msg += (
            f"────────────────────────\n\n"
            f"⚡ {motivo}\n\n"
            f"🔗 {BASE_URL_DASHBOARD_ADMIN}/ticket/{ticket_id}"
        )

    try:
        await send_text_message(admin_whatsapp, msg)
        logger.info(f"✅ Admin notificado ticket #{ticket_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Error notificación #{ticket_id}: {e}")
        return False
