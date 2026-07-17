"""Webhook bidireccional WhatsApp via Meta WhatsApp Cloud API."""
from __future__ import annotations

import logging
import os
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app.agents import route_message, AgentContext
from app.agents.plant_identifier import PlantIdentifierAgent
from app.agents.copilot import get_copilot, get_inventario_snapshot
from app.agents.executor import ejecutar_accion
from app.services.supabase import admin
from app.services.whatsapp_meta import (
    download_media_bytes,
    send_text_message,
    verify_signature,
)
from app.services.onboarding_wa import marcar_primer_producto

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])

# ── NUEVO (Fase 5): número de WhatsApp del admin para notificaciones ─────────
# Se lee de variable de entorno para no hardcodear. Configurar en Vercel:
#   ADMIN_WHATSAPP_NOTIF=+573178543819
ADMIN_WHATSAPP_NOTIF = os.getenv("ADMIN_WHATSAPP_NOTIF", "").strip()
if not ADMIN_WHATSAPP_NOTIF:
    logger.warning(
        "ADMIN_WHATSAPP_NOTIF no configurada — no se enviarán notificaciones "
        "a admin sobre nuevos tickets"
    )

CONFIRMACIONES = {
    "sí", "si", "sí!", "si!", "dale", "ok", "okey", "listo",
    "confirmo", "confirmado", "apruebo", "aprueba", "actualiza",
    "actualizar", "guardar", "guarda", "agregar", "agrega", "yes",
}

# ── NUEVO (Fase 5): mensaje de bienvenida para no-registrados ────────────────
# Reemplaza el mensaje anterior que asumía todos eran viveristas.
BIENVENIDA_NUEVO_USUARIO = (
    "🌱 ¡Hola! Bienvenido a ViveroOnline.com.co\n\n"
    "¿Qué necesitás hoy?\n\n"
    "1️⃣ Comprar plantas vivas\n"
    "2️⃣ Vender mis plantas (soy viverista)\n"
    "3️⃣ Solo consultar\n\n"
    "Escribí 1, 2 o 3 para arrancar."
)

# Respuestas según la opción elegida por el no-registrado
RESPUESTA_OPCION_1_COMPRAR = (
    "🛒 *¡Perfecto! Comprar es fácil:*\n\n"
    "🌿 Andá a nuestra tienda:\n"
    "https://viveroonline.com.co\n\n"
    "Podés comprar sin registrarte, con envío en la Sabana de Bogotá.\n\n"
    "Si necesitás ayuda o tenés dudas, escribinos a "
    "viveroonline.com.co@gmail.com"
)

RESPUESTA_OPCION_2_VENDER = (
    "🌿 *¡Excelente! Vender con nosotros:*\n\n"
    "Creá tu perfil de viverista y empezá a vender:\n"
    "https://viveroonline.com.co/auth/ingresar\n\n"
    "Cuando estés registrado, mandame *ayuda* por acá para ver cómo cargar tus plantas."
)

RESPUESTA_OPCION_3_CONSULTAR = (
    "💬 *¡Con gusto te ayudo!*\n\n"
    "Podés ver precios y catálogo acá:\n"
    "https://viveroonline.com.co\n\n"
    "O si preferís, contame qué necesitás (nombre + consulta) y te contactamos:\n"
    "Ejemplo: 'Soy Ana, quiero saber precio de 200 arizónicas para conjunto en Chía'"
)

RESPUESTA_OPCION_INVALIDA = (
    "🌱 No entendí tu respuesta.\n\n"
    "Por favor escribí *1, 2 o 3* para elegir:\n\n"
    "1️⃣ Comprar plantas vivas\n"
    "2️⃣ Vender mis plantas (soy viverista)\n"
    "3️⃣ Solo consultar"
)

# ── respuestas locales para comprador registrado, sin tocar Gemini ───────────
SALUDOS_COMPRADOR = {
    "hola", "buenas", "buenos dias", "buenos días", "buenas tardes",
    "buenas noches", "hey", "que tal", "qué tal",
}

PATRONES_COMPRADOR_LOCAL = [
    (
        re.compile(r"qu[eé]\s+plantas?\s+me\s+(sugieres|recomiendas|sugerir[ií]as)", re.IGNORECASE),
        (
            "🌿 Para sugerirte plantas necesito saber un poco más de tu proyecto: "
            "¿es para jardín exterior, interior, cerca o un conjunto residencial? "
            "Contame el espacio y la cantidad aproximada, o explorá directo el catálogo:\n"
            "https://app.viveroonline.com.co/marketplace"
        ),
    ),
    (
        re.compile(r"quiero\s+comprar|necesito\s+comprar|comprar\s+(una|un)?\s*planta", re.IGNORECASE),
        (
            "🌿 ¡Buenísimo! Podés explorar el catálogo completo y armar tu cotización aquí:\n"
            "https://app.viveroonline.com.co/marketplace\n\n"
            "Si preferís, contame qué tipo de planta buscás y cuántas necesitás."
        ),
    ),
    (
        re.compile(r"^(que|qué)\s+(haces|hace esto|es esto)", re.IGNORECASE),
        (
            "🌿 Soy el asistente de ViveroOnline. Te ayudo a encontrar plantas, "
            "armar cotizaciones y seguir tus pedidos. Escribí *ayuda* para ver todo "
            "lo que puedo hacer, o entrá directo al marketplace:\n"
            "https://app.viveroonline.com.co/marketplace"
        ),
    ),
]


def _respuesta_local_comprador(body_lower: str) -> str | None:
    if body_lower.strip() in SALUDOS_COMPRADOR:
        return (
            "🌿 ¡Hola! Soy el asistente de ViveroOnline.\n\n"
            "Puedo ayudarte a encontrar plantas para tu proyecto o a seguir tus "
            "pedidos. Escribí *ayuda* para ver los comandos, o explorá el catálogo:\n"
            "https://app.viveroonline.com.co/marketplace"
        )
    for patron, respuesta in PATRONES_COMPRADOR_LOCAL:
        if patron.search(body_lower):
            return respuesta
    return None


# ─── NUEVO (Fase 5): Helpers para tickets de soporte ─────────────────────────

async def _crear_ticket_soporte(
    whatsapp: str,
    tipo_solicitud: str,
    descripcion: str,
    nombre: str | None = None,
    prioridad: str = "media",
) -> int | None:
    """Crea un ticket en tickets_soporte y notifica al admin por WhatsApp.

    tipo_solicitud debe ser uno de: compra, venta, consulta, reclamo, otro.
    prioridad debe ser: baja, media, urgente.

    Retorna el ticket_id creado, o None si falló.
    Errores NO bloquean el flujo del bot (best-effort).
    """
    ticket_id = None
    try:
        db = admin()
        resp = db.table("tickets_soporte").insert({
            "whatsapp_numero": whatsapp,
            "nombre": nombre,
            "tipo_solicitud": tipo_solicitud,
            "descripcion": descripcion[:2000],  # cap por seguridad
            "prioridad": prioridad,
            "estado": "pendiente",
        }).execute()
        if resp.data:
            ticket_id = resp.data[0]["ticket_id"]
            logger.info(f"Ticket #{ticket_id} creado: {tipo_solicitud} desde {whatsapp}")
    except Exception as e:
        logger.error(f"No se pudo crear ticket_soporte: {e}")
        return None

   # Notificar al admin por WhatsApp (best-effort — no bloquea si falla)
    # Solo si ADMIN_WHATSAPP_NOTIF está configurada (ver header del archivo)
    if ticket_id and ADMIN_WHATSAPP_NOTIF:
        try:
            emoji_prioridad = {
                "urgente": "🚨",
                "media": "⚠️",
                "baja": "ℹ️",
            }.get(prioridad, "⚠️")

            msg_admin = (
                f"🎫 *Nuevo ticket #{ticket_id}*\n\n"
                f"📞 Contacto: {whatsapp}\n"
                f"👤 Nombre: {nombre or '(no informado)'}\n"
                f"🏷️ Tipo: {tipo_solicitud}\n"
                f"{emoji_prioridad} Prioridad: {prioridad}\n\n"
                f"📝 _{descripcion[:400]}_\n\n"
                f"Ver en dashboard: https://app.viveroonline.com.co/admin"
            )
            await send_text_message(ADMIN_WHATSAPP_NOTIF, msg_admin)
        except Exception as e:
            logger.warning(f"No se pudo notificar al admin del ticket #{ticket_id}: {e}")

    return ticket_id


# ─── Helpers de sesión ────────────────────────────────────────────────────────

def _find_user_by_whatsapp(whatsapp: str) -> dict | None:
    db = admin()
    resp = db.table("perfiles").select(
        "id, rol, vivero_id, cliente_id, whatsapp_numero, nombre_display"
    ).eq("whatsapp_numero", whatsapp).execute()

    if not resp.data:
        return None
    perfiles = resp.data
    if len(perfiles) == 1:
        return perfiles[0]
    for p in perfiles:
        if p.get("rol") == "viverista" and p.get("vivero_id"):
            return p
    for p in perfiles:
        if p.get("rol") == "comprador" and p.get("cliente_id"):
            return p
    return perfiles[0]


def _get_or_create_session(whatsapp, user_id, rol=None, vivero_id=None, cliente_id=None):
    db = admin()
    existing = db.table("sesiones_agente").select(
        "sesion_id, contexto_json, mensajes_count, accion_pendiente, seleccion_pendiente"
    ).eq("whatsapp_numero", whatsapp).eq("estado", "activa").limit(1).execute()

    if existing.data:
        return existing.data[0]

    new = db.table("sesiones_agente").insert({
        "whatsapp_numero": whatsapp,
        "tipo_usuario": rol or "anonimo",
        "vivero_id": vivero_id,
        "cliente_id": cliente_id,
        "estado": "activa",
        "flujo_actual": "chat",
        "contexto_json": {"historial": []},
        "mensajes_count": 0,
        "fotos_procesadas": 0,
        "accion_pendiente": None,
        "seleccion_pendiente": None,
    }).execute()
    return new.data[0] if new.data else {
        "sesion_id": None, "contexto_json": {"historial": []},
        "mensajes_count": 0, "accion_pendiente": None, "seleccion_pendiente": None,
    }


def _save_message(sesion_id, role, content, agente=None, is_photo=False):
    if not sesion_id:
        return
    db = admin()
    try:
        db.rpc("guardar_mensaje_sesion", {
            "p_sesion_id": sesion_id,
            "p_role": role,
            "p_content": content[:1000],
            "p_es_foto": is_photo,
        }).execute()
    except Exception as e:
        logger.warning(f"No se pudo guardar mensaje en sesión {sesion_id}: {e}")


def _set_accion_pendiente(sesion_id, accion):
    if not sesion_id:
        return
    try:
        admin().table("sesiones_agente").update({
            "accion_pendiente": accion,
            "seleccion_pendiente": None,
        }).eq("sesion_id", sesion_id).execute()
    except Exception as e:
        logger.warning(f"No se pudo guardar accion_pendiente: {e}")


def _set_seleccion_pendiente(sesion_id, seleccion):
    if not sesion_id:
        return
    try:
        admin().table("sesiones_agente").update({
            "seleccion_pendiente": seleccion,
            "accion_pendiente": None,
        }).eq("sesion_id", sesion_id).execute()
    except Exception as e:
        logger.warning(f"No se pudo guardar seleccion_pendiente: {e}")


def _limpiar_pendientes(sesion_id):
    if not sesion_id:
        return
    try:
        admin().table("sesiones_agente").update({
            "accion_pendiente": None,
            "seleccion_pendiente": None,
        }).eq("sesion_id", sesion_id).execute()
    except Exception:
        pass


def _close_session(sesion_id):
    if not sesion_id:
        return
    try:
        admin().table("sesiones_agente").update({
            "estado": "cerrada",
            "fecha_cierre": "now()",
            "accion_pendiente": None,
            "seleccion_pendiente": None,
        }).eq("sesion_id", sesion_id).execute()
    except Exception:
        pass


def _help_text(rol):
    if rol == "viverista":
        return (
            "🌱 *¡Bienvenido a ViveroOnline.com.co!*\n\n"
            "Con nosotros vas a vender tus plantas directamente a "
            "paisajistas, constructoras y conjuntos residenciales de la "
            "Sabana. Sin intermediarios que te bajen el precio.\n\n"
            "*Así de fácil funciona:*\n\n"
            "*1️⃣ Subís tus plantas*\n"
            "Mandame una foto de una planta que tengas en stock. "
            "Yo identifico la especie, sugiero el precio y la agrego a "
            "tu catálogo. Cero papeleo.\n\n"
            "*2️⃣ Recibís cotizaciones*\n"
            "Cuando un cliente quiera comprarte, te llegan las cotizaciones "
            "acá mismo por WhatsApp. Vos decidís: *APROBAR* si te sirve, "
            "*RECHAZAR* si no.\n\n"
            "*3️⃣ Despachás*\n"
            "Cuando el cliente paga, escribís *ENVIADO* al despachar. "
            "Te pagamos en 3-5 días hábiles.\n\n"
            "━━━━━━━━━━━━━━━\n"
            "📚 *Comandos útiles:*\n"
            "• *foto* → identifico y agrego la planta\n"
            "• *precio [planta] [valor]* → cambiar precio\n"
            "• *stock [planta] [cantidad]* → actualizar stock\n"
            "• *agotado [planta]* / *disponible [planta]*\n"
            "• *ayuda* → ver esto de nuevo\n"
            "• *salir* → cerrar sesión\n\n"
            "🚀 *¿Empezamos? Mandame la foto de tu primera planta.*"
        )
    if rol == "comprador":
        return (
            "🌿 *ViveroOnline · Comandos*\n\n"
            "💬 Describí tu proyecto → te recomiendo plantas\n"
            "📷 Enviá foto → identifico la planta\n"
            "🛍️ Catálogo completo: https://app.viveroonline.com.co/marketplace\n\n"
            "Escribí *salir* para cerrar."
        )
    return "🌿 *ViveroOnline*\nEnviame fotos o preguntas sobre plantas.\nEscribí *salir* para cerrar."


# ─── Webhook ──────────────────────────────────────────────────────────────────

@router.get("/webhook")
async def whatsapp_webhook_verify(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    verify_token = os.getenv("META_WA_VERIFY_TOKEN", "").strip()

    # Fail-closed: si el token no está configurado, rechazar TODO
    if not verify_token:
        logger.error("META_WA_VERIFY_TOKEN no configurado — rechazando verificación")
        raise HTTPException(status_code=500, detail="Server misconfigured")

    if mode == "subscribe" and token == verify_token:
        return Response(content=challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verify token inválido")


@router.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    body_bytes = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    if os.getenv("ENV") == "production":
        if not verify_signature(body_bytes, signature):
            logger.warning(f"Firma inválida en webhook. Signature: {signature[:20]}...")
            raise HTTPException(status_code=403, detail="Firma inválida")
    try:
        payload = await request.json()
    except Exception as e:
        # Ya no tragamos el error: lo logueamos para diagnóstico
        logger.error(f"No se pudo parsear payload Meta como JSON: {type(e).__name__}: {e}")
        # Devolvemos 200 igual porque Meta reintenta agresivamente si damos error
        return {"ok": True}
    background_tasks.add_task(_process_payload, payload)
    return {"ok": True}

async def _process_payload(payload: dict):
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue
                value = change.get("value", {})
                if "statuses" in value and "messages" not in value:
                    continue
                for msg in value.get("messages", []) or []:
                    await _handle_message(msg)
    except Exception as e:
        logger.exception(f"Error procesando payload Meta: {e}")


# ─── NUEVO (Fase 5): Handler para NO-REGISTRADO ──────────────────────────────

async def _handle_no_registrado(whatsapp: str, msg_type: str, body: str) -> None:
    """Flujo para usuarios que NO están en perfiles.

    Estados del flujo (guardados en sesiones_agente.accion_pendiente):
      1. Sin sesión previa → mostrar menú 1/2/3 → guardar estado "menu_bienvenida"
      2. Estado "menu_bienvenida" → esperar respuesta 1/2/3
      3. Estado "esperando_solicitud_1|3" → capturar texto y crear ticket
    """
    db = admin()

    # Buscar sesión activa del no-registrado
    existing = db.table("sesiones_agente").select(
        "sesion_id, accion_pendiente"
    ).eq("whatsapp_numero", whatsapp).eq("estado", "activa").limit(1).execute()

    sesion_id = existing.data[0]["sesion_id"] if existing.data else None
    accion_prev = existing.data[0].get("accion_pendiente") if existing.data else None

    # Si envió imagen o algo raro sin sesión → menú
    if msg_type != "text" or not body:
        await send_text_message(whatsapp, BIENVENIDA_NUEVO_USUARIO)
        _guardar_estado_no_registrado(db, whatsapp, sesion_id, "menu_bienvenida")
        return

    lower = body.strip().lower()
    estado_actual = (accion_prev or {}).get("estado_no_reg") if accion_prev else None

    # ── PRIMER MENSAJE (sin sesión previa) ─────────────────────────────────
    if not estado_actual:
        await send_text_message(whatsapp, BIENVENIDA_NUEVO_USUARIO)
        _guardar_estado_no_registrado(db, whatsapp, sesion_id, "menu_bienvenida")
        return

    # ── ESPERANDO RESPUESTA 1/2/3 ──────────────────────────────────────────
    if estado_actual == "menu_bienvenida":
        if lower in ("1", "1️⃣", "comprar", "una", "uno"):
            await send_text_message(whatsapp, RESPUESTA_OPCION_1_COMPRAR)
            _guardar_estado_no_registrado(db, whatsapp, sesion_id, "esperando_solicitud_compra")
            return

        if lower in ("2", "2️⃣", "vender", "viverista", "dos"):
            await send_text_message(whatsapp, RESPUESTA_OPCION_2_VENDER)
            # No requiere ticket — solo enviamos al registro
            _guardar_estado_no_registrado(db, whatsapp, sesion_id, None)
            return

        if lower in ("3", "3️⃣", "consultar", "consulta", "tres"):
            await send_text_message(whatsapp, RESPUESTA_OPCION_3_CONSULTAR)
            _guardar_estado_no_registrado(db, whatsapp, sesion_id, "esperando_solicitud_consulta")
            return

        # Cualquier otra cosa → repetir menú
        await send_text_message(whatsapp, RESPUESTA_OPCION_INVALIDA)
        return

    # ── USUARIO ESTÁ DEJANDO SOLICITUD (después de opción 1 o 3) ───────────
    if estado_actual in ("esperando_solicitud_compra", "esperando_solicitud_consulta"):
        tipo = "compra" if estado_actual == "esperando_solicitud_compra" else "consulta"
        # Intentamos extraer nombre del mensaje (heurística simple)
        nombre = None
        m = re.search(r"soy\s+([A-Za-zÁÉÍÓÚáéíóúÑñ ]{2,40})", body, re.IGNORECASE)
        if m:
            nombre = m.group(1).strip().title()

        ticket_id = await _crear_ticket_soporte(
            whatsapp=whatsapp,
            tipo_solicitud=tipo,
            descripcion=body.strip(),
            nombre=nombre,
            prioridad="media",
        )

        if ticket_id:
            await send_text_message(
                whatsapp,
                f"✅ *¡Recibí tu solicitud! (ticket #{ticket_id})*\n\n"
                "Nuestro equipo te contactará por WhatsApp en las próximas horas.\n\n"
                "Mientras tanto, podés ver el catálogo:\n"
                "https://viveroonline.com.co"
            )
        else:
            await send_text_message(
                whatsapp,
                "⚠️ Tuve un problema guardando tu solicitud. Intentá de nuevo en un momento "
                "o escribinos a viveroonline.com.co@gmail.com"
            )

        _guardar_estado_no_registrado(db, whatsapp, sesion_id, None)
        return

    # Estado desconocido → resetear
    await send_text_message(whatsapp, BIENVENIDA_NUEVO_USUARIO)
    _guardar_estado_no_registrado(db, whatsapp, sesion_id, "menu_bienvenida")


def _guardar_estado_no_registrado(db, whatsapp: str, sesion_id, nuevo_estado: str | None):
    """Guarda el estado del flujo de bienvenida en sesiones_agente.

    Si no hay sesión, la crea. Si nuevo_estado es None, limpia la accion_pendiente.
    """
    accion = {"estado_no_reg": nuevo_estado} if nuevo_estado else None

    if sesion_id:
        try:
            db.table("sesiones_agente").update({
                "accion_pendiente": accion,
            }).eq("sesion_id", sesion_id).execute()
        except Exception as e:
            logger.warning(f"No se pudo actualizar estado no-registrado: {e}")
    else:
        # Crear sesión nueva anónima
        try:
            db.table("sesiones_agente").insert({
                "whatsapp_numero": whatsapp,
                "tipo_usuario": "anonimo",
                "estado": "activa",
                "flujo_actual": "bienvenida",
                "contexto_json": {"historial": []},
                "mensajes_count": 0,
                "fotos_procesadas": 0,
                "accion_pendiente": accion,
            }).execute()
        except Exception as e:
            logger.warning(f"No se pudo crear sesión no-registrado: {e}")


# ─── Handler principal ────────────────────────────────────────────────────────

async def _handle_message(msg: dict):
    msg_type = msg.get("type")
    from_raw = msg.get("from", "")
    whatsapp = f"+{from_raw}" if from_raw and not from_raw.startswith("+") else from_raw
    if not whatsapp:
        return

    user = _find_user_by_whatsapp(whatsapp)

    # ── NUEVO (Fase 5): flujo específico para NO-REGISTRADOS ─────────────────
    if not user:
        body = ""
        if msg_type == "text":
            body = (msg.get("text") or {}).get("body", "").strip()
        await _handle_no_registrado(whatsapp, msg_type, body)
        return

    session = _get_or_create_session(
        whatsapp, user.get("id"),
        rol=user.get("rol"),
        vivero_id=user.get("vivero_id"),
        cliente_id=user.get("cliente_id"),
    )
    sesion_id = session.get("sesion_id")
    rol = user.get("rol")
    vivero_id = user.get("vivero_id")

    if msg_type == "image":
        await _handle_image(msg.get("image", {}).get("id"), user, sesion_id, whatsapp)
        return

    if msg_type == "text":
        body = (msg.get("text") or {}).get("body", "").strip()
        if not body:
            return

        lower = body.lower().strip()

        # Comandos especiales
        if lower in ("ayuda", "help", "menu", "menú"):
            await send_text_message(whatsapp, _help_text(rol))
            return
        if lower in ("salir", "exit", "fin"):
            _close_session(sesion_id)
            await send_text_message(whatsapp, "Sesión cerrada. ¡Hasta pronto! 🌿")
            return

        # ── APROBAR / RECHAZAR cotización desde WhatsApp ─────────────────────
        if rol in ("viverista", "admin"):
            if lower in ("aprobar", "apruebo", "aprobado", "si apruebo"):
                await _handle_aprobar(vivero_id, whatsapp, sesion_id, session)
                return
            if re.match(r'^rechazar|^rechazo', lower):
                motivo = re.sub(r'^rechazar?\s*', '', lower).strip()
                await _handle_rechazar(vivero_id, whatsapp, sesion_id, session, motivo)
                return

        # ── LOGÍSTICA: ENVIADO (viverista despacha) ───────────────────────────
        if rol in ("viverista", "admin") and re.match(r'^enviado', lower):
            await _handle_enviado(body, vivero_id, whatsapp, sesion_id)
            return

        # ── LOGÍSTICA: RECIBIDO (comprador confirma entrega) ──────────────────
        if rol in ("comprador", "admin") and lower in ("recibido", "recibí", "llegó", "llegaron", "recibido ok"):
            await _handle_recibido(user.get("cliente_id"), whatsapp, sesion_id)
            return

        # ── Verificar selección numerada pendiente ───────────────────────────
        seleccion_pendiente = session.get("seleccion_pendiente")
        if seleccion_pendiente and rol in ("viverista", "admin"):
            m = re.match(r'^(\d+)$', lower)
            if m:
                numero = int(m.group(1))
                copilot = get_copilot()
                resultado = copilot.resolver_seleccion(numero, seleccion_pendiente)
                if resultado:
                    respuesta = resultado.get("respuesta", "")
                    acciones = resultado.get("acciones", [])
                    if acciones:
                        _set_accion_pendiente(sesion_id, acciones[0])
                    else:
                        _limpiar_pendientes(sesion_id)
                    _save_message(sesion_id, "model", respuesta, agente="copilot")
                    await send_text_message(whatsapp, respuesta)
                    return

        # ── Verificar acción pendiente de confirmación ────────────────────────
        accion_pendiente = session.get("accion_pendiente")

        # Flujo de datos fiscales / mandato
        if accion_pendiente and accion_pendiente.get("type") == "recolectar_datos_fiscales" and rol in ("viverista", "admin"):
            from app.services.datos_fiscales_wa import procesar_respuesta_datos_fiscales
            resultado_fiscal = await procesar_respuesta_datos_fiscales(
                body, accion_pendiente, vivero_id, whatsapp, sesion_id
            )
            if resultado_fiscal.get("cancelado"):
                _limpiar_pendientes(sesion_id)
            elif resultado_fiscal.get("completado"):
                _limpiar_pendientes(sesion_id)
                cot_id_pendiente = resultado_fiscal.get("cotizacion_id")
                if cot_id_pendiente:
                    await send_text_message(
                        whatsapp,
                        "⏳ Mientras verificamos tus datos, tu aprobación quedó registrada. "
                        "Te confirmamos cuando esté todo listo. 🌿"
                    )
            else:
                _set_accion_pendiente(sesion_id, resultado_fiscal.get("accion", accion_pendiente))
            return

        if accion_pendiente and lower in CONFIRMACIONES and rol in ("viverista", "admin"):
            await _ejecutar_accion_confirmada(accion_pendiente, vivero_id, user.get("id"), sesion_id, whatsapp)
            return

        # Limpiar pendientes si no confirma
        if (accion_pendiente or seleccion_pendiente) and rol in ("viverista", "admin"):
            _limpiar_pendientes(sesion_id)

        # ── Flujo normal ──────────────────────────────────────────────────────
        _save_message(sesion_id, "user", body)
        history = session.get("contexto_json", {}).get("historial", [])
        ctx = AgentContext(
            user_id=user.get("id"),
            whatsapp=whatsapp,
            rol=rol,
            vivero_id=vivero_id,
            cliente_id=user.get("cliente_id"),
            historial=history[-6:],
        )

        # Copilot PRIMERO para viveristas
        if rol in ("viverista", "admin") and vivero_id:
            try:
                inventario = get_inventario_snapshot(vivero_id)
                copilot = get_copilot()
                copilot_result = copilot.procesar(
                    mensaje_usuario=body,
                    respuesta_agente="",
                    ctx=ctx,
                    inventario_snapshot=inventario,
                )
                respuesta_copilot = copilot_result.get("respuesta", "")
                acciones = copilot_result.get("acciones", [])
                seleccion = copilot_result.get("seleccion_pendiente")

                if acciones or seleccion or (respuesta_copilot and not copilot_result.get("_fallback")):
                    if seleccion:
                        _set_seleccion_pendiente(sesion_id, seleccion)
                    elif acciones and not acciones[0].get("needs_clarification"):
                        _set_accion_pendiente(sesion_id, acciones[0])

                    if respuesta_copilot:
                        _save_message(sesion_id, "model", respuesta_copilot, agente="copilot_local")
                        await send_text_message(whatsapp, respuesta_copilot)
                        return

            except Exception as e:
                logger.warning(f"Copilot local error: {e}")

        # Respuestas locales comunes para comprador registrado
        if rol in ("comprador", "admin"):
            respuesta_local = _respuesta_local_comprador(lower)
            if respuesta_local:
                _save_message(sesion_id, "model", respuesta_local, agente="local_comprador")
                await send_text_message(whatsapp, respuesta_local)
                return

        # Fallback: LangGraph
        try:
            result = route_message(body, ctx)
            respuesta_final = result.get("respuesta", "Hubo un problema. Intentá de nuevo.")
            agente = result.get("agente", "ai_ceo")
        except Exception as e:
            if rol in ("comprador", "admin"):
                respuesta_final = (
                    "⏳ Tuve un problema procesando tu mensaje. Mientras lo resolvemos, "
                    "podés explorar el catálogo directamente:\n"
                    "https://app.viveroonline.com.co/marketplace\n\n"
                    "O escribí *ayuda* para ver qué más puedo hacer."
                )
            else:
                respuesta_final = "Disculpá, tuve un problema. Intentá de nuevo en un momento."
            agente = "error"
            logger.warning(f"route_message falló: {e}")

        _save_message(sesion_id, "model", respuesta_final, agente=agente)
        if len(respuesta_final) > 4000:
            respuesta_final = respuesta_final[:3997] + "..."
        await send_text_message(whatsapp, respuesta_final)
        return


    await send_text_message(
        whatsapp,
        "Por ahora solo proceso texto e imágenes 🌿\n"
        "Escribí *ayuda* para ver los comandos disponibles.",
    )


# ─── Ejecutar acción confirmada ───────────────────────────────────────────────

async def _ejecutar_accion_confirmada(accion, vivero_id, user_id, sesion_id, whatsapp):
    _limpiar_pendientes(sesion_id)
    resultado = ejecutar_accion(accion, vivero_id, user_id)

    if accion.get("type") == "agregar_producto":
        try:
            marcar_primer_producto(vivero_id)
        except Exception as e:
            logger.warning(
                f"No se pudo marcar primer_producto vivero {vivero_id}: {e}"
            )

    _save_message(sesion_id, "model", resultado.mensaje, agente="executor")
    await send_text_message(whatsapp, resultado.mensaje)


# ─── Pipeline imagen ──────────────────────────────────────────────────────────

async def _handle_image(image_id, user, sesion_id, whatsapp):
    if not image_id:
        await send_text_message(whatsapp, "No pude acceder a la imagen. Intentá enviarla de nuevo.")
        return

    try:
        image_bytes = await download_media_bytes(image_id)
    except Exception as e:
        await send_text_message(whatsapp, "No pude descargar tu imagen. Intentá de nuevo.")
        logger.error(f"Error descargando media {image_id}: {e}")
        return

    try:
        from app.services.yolo import get_yolo
        image_bytes, _ = await get_yolo().crop_plant(image_bytes)
    except Exception:
        pass

    try:
        analisis = PlantIdentifierAgent().identify_from_bytes(image_bytes, "image/jpeg")
    except RuntimeError as e:
        error_msg = str(e)
        if "cuota_agotada" in error_msg:
            await send_text_message(whatsapp, "⏳ Servicio de IA ocupado. Intentá en unos minutos. 🌿")
        else:
            await send_text_message(whatsapp, "🤔 No pude procesar esta imagen.\n• Más luz\n• Planta centrada\n• Foto directa (no documento)")
        return
    except Exception as e:
        logger.error(f"Error identificando planta: {e}")
        await send_text_message(whatsapp, "🤔 No pude procesar esta imagen. Intentá enviándola directamente desde la cámara.")
        return

    rol = user.get("rol")
    vivero_id = user.get("vivero_id")

    if analisis.confianza < 0.3 or analisis.nombre_comun == "No identificada":
        msg = (
            "🤔 No pude identificar esta planta con confianza.\n\n"
            "Intentá:\n• Más luz natural\n• Planta centrada\n• Sin objetos delante"
        )
        _limpiar_pendientes(sesion_id)
    else:
        precio = analisis.precio_estimado_cop or 0
        precio_str = f"${precio:,} COP" if precio else "a definir"
        precio_comprador = round(precio * 1.18) if precio else 0
        altura = analisis.altura_cm_estimada or 30

        if rol in ("viverista", "admin") and vivero_id:
            foto_url = None
            try:
                import uuid as _uuid
                db = admin()
                filename = f"{vivero_id}/wa_{_uuid.uuid4()}.jpg"
                db.storage.from_("plantas-fotos").upload(
                    path=filename,
                    file=image_bytes,
                    file_options={"content-type": "image/jpeg"},
                )
                foto_url = db.storage.from_("plantas-fotos").get_public_url(filename)
            except Exception as e:
                logger.error(f"Error subiendo foto a Storage: {type(e).__name__}: {e}")

            msg = (
                f"🌿 *{analisis.nombre_comun}*\n"
                f"_{analisis.nombre_cientifico or ''}_\n\n"
                f"📏 Altura: {altura} cm\n"
                f"💰 Tu precio sugerido: {precio_str}\n"
                f"🛒 Comprador pagaría: ${precio_comprador:,} COP\n"
                f"✅ Confianza: {int(analisis.confianza * 100)}%\n\n"
                f"¿La agrego a tu catálogo?\n"
                f"Respondé *SÍ* para confirmar."
            )
            accion_pendiente = {
                "type": "agregar_producto",
                "confirmed": False,
                "params": {
                    "nombre_comun": analisis.nombre_comun,
                    "nombre_cientifico": analisis.nombre_cientifico,
                    "precio_mayorista": precio,
                    "stock": 1,
                    "altura_cm": altura,
                    "foto_url": foto_url,
                    "confianza_yolo": analisis.confianza,
                }
            }
            _set_accion_pendiente(sesion_id, accion_pendiente)
        else:
            msg = (
                f"🌿 *{analisis.nombre_comun}*\n"
                f"_{analisis.nombre_cientifico or ''}_\n\n"
                f"💰 Precio referencia: {precio_str}\n"
                f"☀️ Luz: {analisis.luz or 'N/D'}\n"
                f"💧 Riego: {analisis.riego or 'N/D'}\n"
                f"📏 Altura aprox: {altura} cm\n\n"
                f"✅ Confianza: {int(analisis.confianza * 100)}%\n\n"
                f"¿Querés cotizar esta planta?\n"
                f"Describime cuántas necesitás y dónde."
            )

    _save_message(sesion_id, "user", "[Imagen enviada]", is_photo=True)
    _save_message(sesion_id, "model", msg, agente="plant_identifier")
    await send_text_message(whatsapp, msg)


# ─── Logística: viverista despacha ────────────────────────────────────────────

async def _handle_enviado(body: str, vivero_id: int, whatsapp: str, sesion_id: int):
    from datetime import datetime, timezone
    db = admin()

    entrega_resp = db.table("entregas").select(
        "entrega_id, cotizacion_id, contacto_nombre, contacto_telefono, direccion_entrega"
    ).eq("vivero_id", vivero_id).eq("estado_entrega", "pendiente").order(
        "fecha_creacion", desc=True
    ).limit(1).execute()

    if not entrega_resp.data:
        await send_text_message(
            whatsapp,
            "No encontre entregas pendientes para tu vivero.\n"
            "Revisa el panel: https://app.viveroonline.com.co/viverista"
        )
        return

    entrega = entrega_resp.data[0]
    entrega_id = entrega["entrega_id"]
    cotizacion_id = entrega["cotizacion_id"]

    db.table("entregas").update({
        "estado_entrega": "despachado",
        "fecha_despacho": datetime.now(timezone.utc).isoformat(),
    }).eq("entrega_id", entrega_id).execute()

    msg_viverista = (
        "Despacho registrado\n"
        "Pedido #" + str(cotizacion_id) + " marcado como despachado.\n\n"
        "Entrega en: " + str(entrega.get("direccion_entrega", "")) + "\n"
        "Contacto: " + str(entrega.get("contacto_nombre", "")) + " - " + str(entrega.get("contacto_telefono", "")) + "\n\n"
        "El comprador fue notificado."
    )
    await send_text_message(whatsapp, msg_viverista)

    try:
        cot = db.table("cotizaciones").select(
            "cliente_id, prompt_original"
        ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

        if cot.data:
            cliente_id = cot.data[0]["cliente_id"]
            nombre_proyecto = cot.data[0].get("prompt_original") or "Pedido #" + str(cotizacion_id)
            cliente = db.table("clientes").select("whatsapp_numero").eq(
                "cliente_id", cliente_id
            ).limit(1).execute()

            if cliente.data and cliente.data[0].get("whatsapp_numero"):
                wa_comprador = cliente.data[0]["whatsapp_numero"]
                msg_comprador = (
                    "Tu pedido esta en camino - ViveroOnline\n\n"
                    "Proyecto: " + nombre_proyecto + "\n\n"
                    "Direccion: " + str(entrega.get("direccion_entrega", "")) + "\n"
                    "Contacto en obra: " + str(entrega.get("contacto_nombre", "")) + "\n\n"
                    "Cuando recibas las plantas escribi RECIBIDO para confirmar."
                )
                await send_text_message(wa_comprador, msg_comprador)
    except Exception as e:
        logger.warning("No se pudo notificar al comprador: " + str(e))


# ─── Logística: comprador confirma recibo ─────────────────────────────────────

async def _handle_recibido(cliente_id: int, whatsapp: str, sesion_id: int):
    from datetime import datetime, timezone

    if not cliente_id:
        await send_text_message(whatsapp, "No pude identificar tu perfil. Intenta de nuevo.")
        return

    db = admin()

    entrega_resp = db.table("entregas").select(
        "entrega_id, cotizacion_id, vivero_id"
    ).eq("estado_entrega", "despachado").limit(10).execute()

    entrega = None
    for e in entrega_resp.data or []:
        cot = db.table("cotizaciones").select("cliente_id").eq(
            "cotizacion_id", e["cotizacion_id"]
        ).limit(1).execute()
        if cot.data and cot.data[0]["cliente_id"] == cliente_id:
            entrega = e
            break

    if not entrega:
        await send_text_message(
            whatsapp,
            "No encontre entregas en camino para confirmar.\n"
            "Revisa tu panel: https://app.viveroonline.com.co/comprador"
        )
        return

    entrega_id = entrega["entrega_id"]
    cotizacion_id = entrega["cotizacion_id"]
    vivero_id = entrega["vivero_id"]

    db.table("entregas").update({
        "estado_entrega": "entregado",
        "fecha_entrega": datetime.now(timezone.utc).isoformat(),
    }).eq("entrega_id", entrega_id).execute()

    await send_text_message(
        whatsapp,
        "Entrega confirmada!\n"
        "Gracias por confirmar. Esperamos que todo haya llegado perfecto.\n\n"
        "Explora el marketplace:\nhttps://app.viveroonline.com.co/marketplace"
    )

    try:
        vivero = db.table("viveros").select(
            "nombre_vivero, whatsapp_numero"
        ).eq("vivero_id", vivero_id).limit(1).execute()

        if vivero.data and vivero.data[0].get("whatsapp_numero"):
            cot = db.table("cotizaciones").select(
                "total_estimado, prompt_original"
            ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

            total = 0
            nombre_proyecto = "Pedido #" + str(cotizacion_id)
            if cot.data:
                total = int(float(cot.data[0].get("total_estimado") or 0))
                nombre_proyecto = cot.data[0].get("prompt_original") or nombre_proyecto

            msg_viverista = (
                "Entrega confirmada - ViveroOnline\n\n"
                "Proyecto: " + nombre_proyecto + "\n"
                "El comprador confirmo que recibio las plantas.\n\n"
                "Tu pago de $" + "{:,}".format(total) + " COP se procesa en 48 horas."
            )
            await send_text_message(vivero.data[0]["whatsapp_numero"], msg_viverista)
    except Exception as e:
        logger.warning("No se pudo notificar al viverista: " + str(e))


# ─── Aprobar cotización desde WhatsApp ────────────────────────────────────────

async def _handle_aprobar(vivero_id: int, whatsapp: str, sesion_id: int, session: dict):
    from app.routes.pedidos import aprobar_subcotizacion_vivero
    from app.services.supabase import admin as db_admin
    from app.services.datos_fiscales_wa import (
        vivero_necesita_datos_fiscales,
        iniciar_flujo_mandato,
    )

    accion = session.get("accion_pendiente") or {}

    if accion.get("type") != "aprobar_rechazar_cotizacion":
        await send_text_message(
            whatsapp,
            "No tengo ninguna cotización pendiente de aprobar.\n"
            "Revisa tu panel: https://app.viveroonline.com.co/viverista"
        )
        return

    cotizacion_id = accion.get("params", {}).get("cotizacion_id")

    if not cotizacion_id:
        await send_text_message(whatsapp, "Error: no encontré el ID de la cotización.")
        return

    db = db_admin()

    if vivero_necesita_datos_fiscales(vivero_id):
        await iniciar_flujo_mandato(vivero_id, whatsapp, sesion_id, cotizacion_id)
        return

    resultado = await aprobar_subcotizacion_vivero(db, cotizacion_id, vivero_id)

    _limpiar_pendientes(sesion_id)

    if not resultado["ok"]:
        motivo = resultado.get("motivo")
        if motivo == "estado_cambio_antes_del_update":
            await send_text_message(
                whatsapp,
                "⚠️ Esta cotización cambió de estado justo antes de tu aprobación "
                "(por ejemplo, venció). Revisá el panel para ver el estado actual:\n"
                "https://app.viveroonline.com.co/viverista"
            )
        elif motivo == "ya_procesada":
            await send_text_message(
                whatsapp,
                f"Esta cotización ya fue procesada (estado: {resultado.get('estado_actual')})."
            )
        else:
            await send_text_message(
                whatsapp,
                "No pude aprobar esta cotización. Revisá el panel para más detalle:\n"
                "https://app.viveroonline.com.co/viverista"
            )
        return

    nombre_proyecto = resultado.get("nombre_proyecto", f"Cotización #{cotizacion_id}")

    if resultado["estado"] == "aceptada":
        await send_text_message(
            whatsapp,
            "Cotizacion aprobada\n\n"
            "Proyecto: " + nombre_proyecto + "\n"
            "El comprador recibira la notificacion para pagar.\n\n"
            "Te avisamos cuando el pago sea confirmado."
        )
    else:
        await send_text_message(
            whatsapp,
            "Tu aprobación quedó registrada\n\n"
            "Proyecto: " + nombre_proyecto + "\n"
            f"Aprobaron {resultado.get('aprobadas')} de {resultado.get('total_viveros')} viveros.\n"
            "Avisaremos al comprador cuando todos confirmen."
        )


# ─── Rechazar cotización desde WhatsApp ──────────────────────────────────────

async def _handle_rechazar(vivero_id: int, whatsapp: str, sesion_id: int, session: dict, motivo: str = ""):
    from app.routes.pedidos import rechazar_subcotizacion_vivero
    from app.services.supabase import admin as db_admin

    accion = session.get("accion_pendiente") or {}

    if accion.get("type") != "aprobar_rechazar_cotizacion":
        await send_text_message(
            whatsapp,
            "No tengo ninguna cotizacion pendiente de rechazar.\n"
            "Revisa tu panel: https://app.viveroonline.com.co/viverista"
        )
        return

    cotizacion_id = accion.get("params", {}).get("cotizacion_id")

    if not cotizacion_id:
        await send_text_message(whatsapp, "Error: no encontre el ID de la cotizacion.")
        return

    db = db_admin()
    resultado = await rechazar_subcotizacion_vivero(db, cotizacion_id, vivero_id, motivo or None)

    _limpiar_pendientes(sesion_id)

    if not resultado["ok"]:
        motivo_error = resultado.get("motivo")
        if motivo_error == "estado_cambio_antes_del_update":
            await send_text_message(
                whatsapp,
                "⚠️ Esta cotización cambió de estado justo antes de tu rechazo "
                "(por ejemplo, ya fue aprobada por otro proceso). Revisá el panel:\n"
                "https://app.viveroonline.com.co/viverista"
            )
        elif motivo_error == "ya_procesada":
            await send_text_message(
                whatsapp,
                f"Esta cotización ya fue procesada (estado: {resultado.get('estado_actual')})."
            )
        else:
            await send_text_message(
                whatsapp,
                "No pude rechazar esta cotización. Revisá el panel para más detalle:\n"
                "https://app.viveroonline.com.co/viverista"
            )
        return

    nombre_proyecto = resultado.get("nombre_proyecto", f"Cotizacion #{cotizacion_id}")

    await send_text_message(
        whatsapp,
        "Cotizacion rechazada\n\n"
        "Proyecto: " + nombre_proyecto + "\n"
        "El comprador fue notificado."
        + (
            "\n\nEncontramos vivero(s) alternativo(s) para el comprador."
            if resultado.get("alternativas") else ""
        )
    )
