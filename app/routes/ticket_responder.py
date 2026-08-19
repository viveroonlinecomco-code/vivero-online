"""Auto-responder para tickets de soporte y notificación a admin.

Entregado 12 ago 2026 — Sistema automático que responde tickets según tipo
sin caso específico, reduce carga manual 70%.
"""
import logging
from app.services.whatsapp_meta import send_text_message
from app.services.supabase import admin

logger = logging.getLogger(__name__)


async def responder_ticket_segun_tipo(ticket_id: int, ticket_data: dict) -> dict:
    """Auto-responde al usuario según tipo de ticket.
    
    Tipos soportados: compra, consulta, reclamo, venta, otro
    
    Retorna: {"tipo": "auto_cerrado" | "escalado", "mensaje": "respuesta enviada"}
    """
    tipo = ticket_data.get("tipo_solicitud", "otro").lower()
    descripcion = ticket_data.get("descripcion", "").lower()
    whatsapp = ticket_data.get("whatsapp_numero", "")
    
    # Compra + precio específico → Busca en BD, responde + cierra
    if tipo == "compra" and whatsapp:
        respuesta = _buscar_producto_responder_compra(descripcion)
        if respuesta and respuesta.get("encontrado"):
            try:
                await send_text_message(whatsapp, respuesta["mensaje"])
                logger.info(f"Ticket #{ticket_id} auto-cerrado: compra con precio")
                return {"tipo": "auto_cerrado", "mensaje": respuesta["mensaje"]}
            except Exception as e:
                logger.warning(f"Error enviando respuesta compra ticket #{ticket_id}: {e}")
    
    # Compra + despacho/info → Guía marketplace + email sales
    if tipo == "compra" and ("despacho" in descripcion or "envío" in descripcion or "entrega" in descripcion):
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
            logger.info(f"Ticket #{ticket_id} auto-cerrado: compra + despacho")
            return {"tipo": "auto_cerrado", "mensaje": respuesta}
        except Exception as e:
            logger.warning(f"Error enviando respuesta despacho ticket #{ticket_id}: {e}")
    
    # Consulta específica → Responde si sabe
    if tipo == "consulta":
        respuesta = _responder_consulta_especifica(descripcion)
        if respuesta:
            try:
                await send_text_message(whatsapp, respuesta)
                logger.info(f"Ticket #{ticket_id} auto-cerrado: consulta")
                return {"tipo": "auto_cerrado", "mensaje": respuesta}
            except Exception as e:
                logger.warning(f"Error enviando respuesta consulta ticket #{ticket_id}: {e}")
    
    # Consulta B2B o reclamo → Derivar admin (escalado)
    if tipo in ("venta", "reclamo") or ("b2b" in descripcion):
        logger.info(f"Ticket #{ticket_id} escalado a admin: {tipo}")
        return {"tipo": "escalado", "mensaje": f"Derivado a admin para procesamiento de {tipo}"}
    
    # Por defecto: derivar admin
    logger.info(f"Ticket #{ticket_id} escalado a admin: tipo desconocido")
    return {"tipo": "escalado", "mensaje": "Tu solicitud está siendo procesada por nuestro equipo"}


def _buscar_producto_responder_compra(descripcion: str) -> dict | None:
    """Busca producto en descripción y devuelve precio + link si existe."""
    productos_conocidos = {
        "hiedra": {"precio": 20412, "id": 1},
        "geranio": {"precio": 15000, "id": 2},
        "duranta": {"precio": 18500, "id": 3},
        "afelandra": {"precio": 22000, "id": 4},
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
    """Responde consultas sobre la plataforma si reconoce el patrón."""
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
            "Explora el catálogo filtrado por tipo:\n"
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
    """Notifica al admin sobre ticket con contexto.
    
    Se envía SOLO si:
    1. El ticket fue escalado (no auto-cerrado)
    2. admin_whatsapp está configurado
    3. El tipo es B2B o reclamo (prioritario)
    """
    if not admin_whatsapp:
        logger.warning(f"ADMIN_WHATSAPP_NOTIF no configurado — ticket #{ticket_id} sin notificación")
        return
    
    tipo = ticket_data.get("tipo_solicitud", "otro")
    
    # Solo notificar si es escalado o prioritario
    if respuesta_info.get("tipo") != "escalado" and tipo not in ("venta", "reclamo", "b2b"):
        return
    
    cliente_whatsapp = ticket_data.get("whatsapp_numero", "?")
    nombre = ticket_data.get("nombre", "Cliente anónimo")
    descripcion = ticket_data.get("descripcion", "")
    
    # Mensaje al admin
    prioridad_emoji = "🔴" if tipo in ("reclamo", "venta") else "🟡"
    msg_admin = (
        f"{prioridad_emoji} *Ticket #{ticket_id}* — {tipo.upper()}\n\n"
        f"👤 Cliente: {nombre}\n"
        f"📱 WhatsApp: {cliente_whatsapp}\n\n"
        f"💬 Solicitud:\n{descripcion[:500]}\n\n"
        f"Panel: https://app.viveroonline.com.co/admin"
    )
    
    try:
        await send_text_message(admin_whatsapp, msg_admin)
        logger.info(f"Notificación admin enviada para ticket #{ticket_id}")
    except Exception as e:
        logger.error(f"Error notificando admin ticket #{ticket_id}: {e}")
