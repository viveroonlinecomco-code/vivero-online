"""Sistema de respuesta automática de tickets de soporte.

Procesa tickets según tipo_solicitud y proporciona respuestas canned
o guía al usuario al marketplace/admin.

Tipos de tickets soportados:
  - compra: Guiar a marketplace o devolver precio específico
  - consulta: Responder preguntas o derivar a admin
  - reclamo: Derivar a admin inmediatamente
  - venta: Derivar a admin inmediatamente
  - otro: Derivar a admin

Respuestas canned (plantillas automáticas):
  - Precio de producto: Buscar en BD, devolver + link
  - Despacho/zona: Explicar cobertura geográfica
  - Materas: Link a categoría en marketplace
  - Sobre empresa: Link a /quienes-somos
  - Productos genéricos: Link a marketplace

Post-respuesta:
  - Si respuesta automática completa → cerrar ticket con estado "respondido"
  - Si no se pudo auto-responder → mantener "pendiente" + notificar admin
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.services.supabase import admin as db_admin
from app.services.whatsapp_meta import send_text_message

logger = logging.getLogger(__name__)


def _normalizar_nombre_planta(texto: str) -> str:
    """Extrae nombre de planta del texto (busca palabras que NO sean artículos/preposiciones)."""
    # Palabras a ignorar
    stopwords = {"el", "la", "de", "del", "a", "para", "por", "en", "con", "sin"}
    palabras = [w.strip().lower() for w in re.split(r"[\s,;./]", texto) if w.strip()]
    palabras = [w for w in palabras if w not in stopwords and len(w) > 2]
    return " ".join(palabras[:3]) if palabras else texto.lower()


async def responder_ticket_segun_tipo(ticket_id: int, ticket_data: dict) -> dict:
    """
    Responde ticket automáticamente según tipo_solicitud.

    Retorna:
    {
        "respondido": bool,
        "auto_respuesta": str o None,
        "accion": "cerrar" | "derivar_admin" | "pendiente",
        "motivo": str
    }
    """
    db = db_admin()
    whatsapp = ticket_data.get("whatsapp_numero")
    tipo = ticket_data.get("tipo_solicitud", "").lower()
    desc = (ticket_data.get("descripcion") or "").lower()
    nombre = ticket_data.get("nombre") or "Cliente"

    respuesta = None
    accion = "derivar_admin"  # default
    motivo = ""

    # ═══════════════════════════════════════════════════════════════════
    # 1. TIPO: COMPRA — Guiar a marketplace o responder precio específico
    # ═══════════════════════════════════════════════════════════════════

    if tipo == "compra":
        # Caso 1a: Pregunta por precio de producto específico
        # Patrones: "precio de X", "cuánto cuesta X", "valor de X", "acanto", "buganvilias", etc
        match_precio = re.search(
            r"(?:precio|cuesta|valor|tamaño|cuánto|valen?|costo|tarifa).*?(?:de\s+)?([a-záéíóúñ\s]+?)(?:\?|$)",
            desc,
        )
        if match_precio:
            nombre_planta = _normalizar_nombre_planta(match_precio.group(1))
            # Buscar en catalogo
            plantas_resp = db.table("plantas").select(
                "nombre_comun, nombre_cientifico, precio_base"
            ).ilike("nombre_comun", f"%{nombre_planta}%").limit(3).execute()

            if plantas_resp.data:
                planta = plantas_resp.data[0]
                nombre_comun = planta.get("nombre_comun", "Planta")
                precio = planta.get("precio_base", 0)
                respuesta = (
                    f"🌿 *{nombre_comun}*\n\n"
                    f"💰 Precio desde: ${int(precio):,} COP\n\n"
                    f"🛒 Explorá todas nuestras plantas:\n"
                    f"https://viveroonline.com.co/marketplace\n\n"
                    f"¿Necesitás más información? Escribinos por acá o "
                    f"contactá a sales@viveroonline.com.co"
                )
                accion = "cerrar"
            else:
                # No encontramos en BD, guiar a marketplace
                respuesta = (
                    f"🌿 ¡Hola {nombre}!\n\n"
                    f"No encontramos '{nombre_planta}' en nuestra búsqueda rápida, "
                    f"pero probablemente la tengamos disponible.\n\n"
                    f"🛒 Explorá nuestro catálogo completo:\n"
                    f"https://viveroonline.com.co/marketplace\n\n"
                    f"Si no la encuentras, escribinos con el nombre exacto y "
                    f"te damos un presupuesto personalizado. 😊"
                )
                accion = "cerrar"

        # Caso 1b: Pregunta por despacho/zona
        elif re.search(r"despacho|envío|zona|costo de envío|llega", desc):
            respuesta = (
                f"🚐 *Entregas en la Sabana de Bogotá*\n\n"
                f"Hacemos entregas a: Bogotá, Chía, Cajicá, Cota, "
                f"Tabio, Tenjo, Madrid, Zipaquirá y alrededores.\n\n"
                f"💰 Costo de envío: Desde $35.000 COP según distancia\n\n"
                f"🛒 Armá tu pedido aquí:\n"
                f"https://viveroonline.com.co/marketplace\n\n"
                f"¿Tu zona no está? Escribinos para consultar. 😊"
                )
                accion = "cerrar"

        # Caso 1c: Pregunta por producto genérico (materas, flores, plantas)
        elif re.search(r"materas|flores|plantas|tienes", desc):
            respuesta = (
                f"🌿 ¡Hola {nombre}!\n\n"
                f"Tenemos amplio catálogo de plantas, materas y accesorios. "
                f"👇 Explorá todo lo que ofrecemos:\n\n"
                f"🛒 https://viveroonline.com.co/marketplace\n\n"
                f"¿Necesitás una cotización personalizada o tenés dudas? "
                f"Respondé por acá o contactá sales@viveroonline.com.co"
            )
            accion = "cerrar"

        # Caso 1d: Pregunta sobre la empresa / info general
        else:
            respuesta = (
                f"🌿 ¡Hola {nombre}!\n\n"
                f"Gracias por tu interés en ViveroOnline. 🙌\n\n"
                f"🛒 Explorá nuestro catálogo:\n"
                f"https://viveroonline.com.co/marketplace\n\n"
                f"¿Preguntas? Escribinos aquí o contactá "
                f"sales@viveroonline.com.co"
            )
            accion = "cerrar"

    # ═══════════════════════════════════════════════════════════════════
    # 2. TIPO: CONSULTA — Responder o derivar
    # ═══════════════════════════════════════════════════════════════════

    elif tipo == "consulta":
        # Caso 2a: Pregunta sobre datos de la empresa
        if re.search(r"quiénes\s+somos|sobre\s+(ustedes|nosotros)|empresa", desc):
            respuesta = (
                f"🌱 *Sobre ViveroOnline*\n\n"
                f"Somos un marketplace B2B que conecta viveristas con paisajistas "
                f"y constructoras en la región.\n\n"
                f"📄 Conocé más:\n"
                f"https://viveroonline.com.co/quienes-somos\n\n"
                f"¿Querés hablar con nuestro equipo? "
                f"Escribinos a sales@viveroonline.com.co 😊"
            )
            accion = "cerrar"

        # Caso 2b: Solicitud B2B compleja (servicios, proveedores, etc)
        elif re.search(r"servicio|proveedor|facturación|arl|eps|b2b", desc):
            respuesta = (
                f"🌿 *Consulta Comercial*\n\n"
                f"Gracias por tu interés en trabajar con nosotros. 🙌\n\n"
                f"Tu consulta es importante y requiere seguimiento personalizado. "
                f"Pronto te contactaremos.\n\n"
                f"Contacto directo: sales@viveroonline.com.co"
            )
            accion = "derivar_admin"  # Notificar admin para seguimiento
            motivo = "Solicitud B2B comercial — requiere análisis"

        # Caso 2c: Otra consulta
        else:
            respuesta = None
            accion = "derivar_admin"
            motivo = "Consulta específica — requiere análisis"

    # ═══════════════════════════════════════════════════════════════════
    # 3. TIPO: RECLAMO, VENTA, OTRO — Siempre derivar
    # ═══════════════════════════════════════════════════════════════════

    elif tipo in ("reclamo", "venta", "otro"):
        if tipo == "reclamo":
            respuesta = (
                f"🔴 *Reclamo recibido*\n\n"
                f"Lamentamos los inconvenientes. Nuestro equipo te contactará "
                f"pronto para resolver tu situación.\n\n"
                f"Contacto de urgencia: sales@viveroonline.com.co"
            )
            accion = "derivar_admin"
            motivo = "Reclamo — requiere atención urgente"
        elif tipo == "venta":
            respuesta = (
                f"🌿 *Interés en vender con nosotros*\n\n"
                f"¡Excelente! Nuestro equipo te contactará para explicarte "
                f"cómo formar parte de la plataforma.\n\n"
                f"Contacto: sales@viveroonline.com.co"
            )
            accion = "derivar_admin"
            motivo = "Nuevo viverista interesado — onboarding"
        else:
            respuesta = None
            accion = "derivar_admin"
            motivo = f"Ticket tipo '{tipo}' — requiere análisis"

    # ═════════════════════════════════════════════════════════════════════
    # Enviar respuesta por WhatsApp si existe
    # ═════════════════════════════════════════════════════════════════════

    respondido = False
    if respuesta and whatsapp:
        try:
            await send_text_message(whatsapp, respuesta)
            respondido = True
            logger.info(f"✅ Respuesta automática enviada al ticket #{ticket_id}")
        except Exception as e:
            logger.error(f"No se pudo enviar respuesta al ticket #{ticket_id}: {e}")
            respondido = False

    # ═════════════════════════════════════════════════════════════════════
    # Actualizar estado del ticket
    # ═════════════════════════════════════════════════════════════════════

    if accion == "cerrar" and respondido:
        # Cerrar ticket automáticamente
        db.table("tickets_soporte").update({
            "estado": "respondido",
            "atendido_por": "bot_autorespuesta",
            "notas_admin": "Respuesta automática enviada por bot",
        }).eq("ticket_id", ticket_id).execute()
        logger.info(f"✅ Ticket #{ticket_id} cerrado automáticamente")

    elif accion == "derivar_admin":
        # Dejar en pendiente pero notificar admin
        db.table("tickets_soporte").update({
            "notas_admin": f"AUTO-DERIVAR: {motivo}",
        }).eq("ticket_id", ticket_id).execute()

    return {
        "respondido": respondido,
        "auto_respuesta": respuesta,
        "accion": accion,
        "motivo": motivo,
    }


async def notificar_admin_ticket_con_respuesta(
    ticket_id: int,
    ticket_data: dict,
    respuesta_info: dict,
    admin_whatsapp: str,
) -> bool:
    """
    Notifica al admin cuando:
    - Un ticket fue respondido automáticamente (info)
    - Un ticket requiere derivación (alerta)
    """
    tipo = ticket_data.get("tipo_solicitud", "").upper()
    whatsapp_cliente = ticket_data.get("whatsapp_numero", "")
    desc = (ticket_data.get("descripcion") or "")[:100]
    accion = respuesta_info.get("accion")

    if accion == "cerrar":
        # Notificación informativa
        msg = (
            f"✅ *Ticket #{ticket_id} respondido automáticamente*\n\n"
            f"📞 {whatsapp_cliente}\n"
            f"🏷️ Tipo: {tipo}\n"
            f"📝 _{desc}_\n\n"
            f"Estado: CERRADO"
        )
    else:
        # Alerta para derivación
        msg = (
            f"⚠️ *Ticket #{ticket_id} requiere atención*\n\n"
            f"📞 {whatsapp_cliente}\n"
            f"🏷️ Tipo: {tipo}\n"
            f"📝 _{desc}_\n\n"
            f"Acción: {respuesta_info.get('motivo', 'Revisar y responder')}\n\n"
            f"Ver en dashboard: https://app.viveroonline.com.co/admin"
        )

    try:
        await send_text_message(admin_whatsapp, msg)
        logger.info(f"✅ Admin notificado sobre ticket #{ticket_id}")
        return True
    except Exception as e:
        logger.error(f"No se pudo notificar admin del ticket #{ticket_id}: {e}")
        return False
