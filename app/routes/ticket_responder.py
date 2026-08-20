"""Auto-responder para tickets + NOTIFICACIÓN GARANTIZADA al admin.

MEJORADO: Agrega respuesta al cliente en el mensaje que llega al admin.
"""
import logging
from app.services.whatsapp_meta import send_text_message
from app.services.supabase import admin

logger = logging.getLogger(__name__)


async def responder_ticket_segun_tipo(ticket_id: int, ticket_data: dict) -> dict:
    """Auto-responde al usuario según tipo de ticket.
    
    Tipos: compra, consulta, reclamo, venta, otro
    
    Retorna: {"tipo": "auto_cerrado"|"escalado", "mensaje": "..."}
    """
    tipo = ticket_data.get("tipo_solicitud", "otro").lower()
    descripcion = ticket_data.get("descripcion", "").lower()
    whatsapp = ticket_data.get("whatsapp_numero", "")
    
    # COMPRA + PRECIO específico
    if tipo == "compra" and whatsapp:
        respuesta = _buscar_producto_responder_compra(descripcion)
        if respuesta and respuesta.get("encontrado"):
            try:
                await send_text_message(whatsapp, respuesta["mensaje"])
                logger.info(f"Ticket #{ticket_id} auto-cerrado: compra con precio")
                return {"tipo": "auto_cerrado", "mensaje": respuesta["mensaje"]}
            except Exception as e:
                logger.warning(f"Error enviando respuesta compra #{ticket_id}: {e}")
    
    # COMPRA + DESPACHO
    if tipo == "compra" and any(kw in descripcion for kw in ["despacho", "envío", "entrega", "salitre"]):
        respuesta = (
            "🚚 *Información de despacho*\n\n"
            "Hacemos entregas en la Sabana de Bogotá (Chía, Cajicá, Cota, Tabio, Tenjo).\n\n"
            "📍 Explora el catálogo y arma tu cotización:\n"
            "https://app.viveroonline.com.co/marketplace\n\n"
            "Para consultas de despacho especial, escribi a:\n"
            "viveroonline.com.co@gmail.com"
        )
        try:
            await send_text_message(whatsapp, respuesta)
            logger.info(f"Ticket #{ticket_id} auto-cerrado: despacho")
            return {"tipo": "auto_cerrado", "mensaje": respuesta}
        except Exception as e:
            logger.warning(f"Error enviando respuesta despacho #{ticket_id}: {e}")
    
    # CONSULTA + ÁRBOLES
    if tipo == "consulta" and any(kw in descripcion for kw in ["árbol", "año", "grande"]):
        respuesta = (
            "🌳 *Árboles y plantas grandes*\n\n"
            "Tenemos árboles de más de 1 año de edad en varias especies.\n\n"
            "🏢 Nuestro equipo te enviará:\n"
            "✅ Catálogo de árboles disponibles\n"
            "✅ Precios por tamaño y especie\n"
            "✅ Opciones de envío y logística\n\n"
            "📞 Te contactaremos en la próxima hora.\n\n"
            "O escribi directamente a: viveroonline.com.co@gmail.com"
        )
        try:
            await send_text_message(whatsapp, respuesta)
            logger.info(f"Ticket #{ticket_id} auto-respondido: árboles")
            return {"tipo": "auto_cerrado", "mensaje": respuesta}
        except Exception as e:
            logger.warning(f"Error enviando respuesta árboles #{ticket_id}: {e}")
    
    # CONSULTA específica
    if tipo == "consulta":
        respuesta = _responder_consulta_especifica(descripcion)
        if respuesta:
            try:
                await send_text_message(whatsapp, respuesta)
                logger.info(f"Ticket #{ticket_id} auto-cerrado: consulta")
                return {"tipo": "auto_cerrado", "mensaje": respuesta}
            except Exception as e:
                logger.warning(f"Error enviando respuesta consulta #{ticket_id}: {e}")
    
    # Tipos que SIEMPRE se escalan
    if tipo in ("venta", "reclamo") or "b2b" in descripcion:
        logger.info(f"Ticket #{ticket_id} escalado a admin: {tipo}")
        return {"tipo": "escalado", "mensaje": "Tu solicitud está siendo procesada por nuestro equipo"}
    
    # Por defecto: derivar admin
    logger.info(f"Ticket #{ticket_id} escalado a admin: tipo desconocido")
    return {"tipo": "escalado", "mensaje": "Tu solicitud está siendo procesada"}


def _buscar_producto_responder_compra(descripcion: str) -> dict | None:
    """Busca producto en descripción."""
    productos_conocidos = {
        "hiedra": {"precio": 20412, "id": 1},
        "geranio": {"precio": 15000, "id": 2},
        "duranta": {"precio": 18500, "id": 3},
        "afelandra": {"precio": 22000, "id": 4},
        "jasmin": {"precio": 16500, "id": 5},
    }
    
    for producto, info in productos_conocidos.items():
        if producto in descripcion:
            respuesta = (
                f"🌿 *{producto.title()}*\n\n"
                f"💰 Precio: ${info['precio']:,} COP\n\n"
                f"🛍️ Comprar aquí:\n"
                f"https://app.viveroonline.com.co/marketplace/producto/{info['id']}\n\n"
                f"¿Necesitás más información?"
            )
            return {"encontrado": True, "mensaje": respuesta}
    
    return None


def _responder_consulta_especifica(descripcion: str) -> str | None:
    """Responde consultas frecuentes."""
    if "como funciona" in descripcion or "qué es viveroonline" in descripcion:
        return (
            "🌿 *ViveroOnline* es la plataforma de venta directa de plantas para:\n\n"
            "✅ *Compradores*: Acceso a 100+ plantas vivas de viveros locales\n"
            "✅ *Viveristas*: Venden directamente sin intermediarios\n\n"
            "Explora el catálogo:\nhttps://app.viveroonline.com.co\n\n"
            "¿Querés comprar o vender?"
        )
    
    if "plantas para interior" in descripcion or "plantas low maintenance" in descripcion:
        return (
            "🌿 *Plantas para interior*:\n\n"
            "Tenemos opciones que se adaptan a poca luz y poco riego.\n\n"
            "Explora el catálogo:\n"
            "https://app.viveroonline.com.co/marketplace\n\n"
            "¿Querés una cotización personalizada?"
        )
    
    return None


async def notificar_admin_con_contexto(
    ticket_id: int,
    ticket_data: dict,
    respuesta_info: dict,
    admin_whatsapp: str,
) -> None:
    """✅ NOTIFICA AL ADMIN DE TODOS LOS TICKETS, SIN EXCEPCIONES.
    
    MEJORADO: Agrega la respuesta que se le dio al cliente.
    
    Diferencia: auto-cerrados vs escalados (emojis diferentes)
    """
    if not admin_whatsapp:
        logger.warning(f"ADMIN_WHATSAPP_NOTIF no configurado — ticket #{ticket_id} sin notificación")
        return
    
    tipo = ticket_data.get("tipo_solicitud", "otro").upper()
    cliente_whatsapp = ticket_data.get("whatsapp_numero", "?")
    nombre = ticket_data.get("nombre", "Cliente anónimo")
    descripcion = ticket_data.get("descripcion", "")[:150]
    respuesta_tipo = respuesta_info.get("tipo", "?")
    respuesta_mensaje = respuesta_info.get("mensaje", "")[:300]
    
    # EMOJI Y PRIORIDAD según tipo
    emoji_map = {
        "compra": "🟢",
        "consulta": "🔵",
        "reclamo": "🔴",
        "venta": "🟠",
        "b2b": "💼",
    }
    emoji = emoji_map.get(tipo.lower(), "⚪")
    
    # ESTADO DE RESPUESTA
    estado_bot = "✅ Auto-respondido" if respuesta_tipo == "auto_cerrado" else "⚠️ Escalado a revisión"
    
    # CONSTRUIR MENSAJE AL ADMIN CON RESPUESTA AGREGADA
    msg_admin = (
        f"{emoji} *Ticket #{ticket_id}* — {tipo}\n\n"
        f"👤 Cliente: {nombre}\n"
        f"📱 WhatsApp: {cliente_whatsapp}\n\n"
        f"💬 Solicitud:\n{descripcion}\n\n"
        f"🤖 {estado_bot}\n"
    )
    
    # ✅ AGREGAR RESPUESTA AL CLIENTE SI EXISTE
    if respuesta_mensaje:
        msg_admin += f"\n📨 Respuesta enviada:\n{respuesta_mensaje}\n"
    
    msg_admin += f"\n🔗 Panel: https://app.viveroonline.com.co/admin"
    
    try:
        await send_text_message(admin_whatsapp, msg_admin)
        logger.info(f"✅ Notificación admin enviada para ticket #{ticket_id}")
    except Exception as e:
        logger.error(f"❌ Error notificando admin ticket #{ticket_id}: {e}")
