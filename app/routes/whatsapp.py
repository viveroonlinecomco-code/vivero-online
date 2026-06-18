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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])

CONFIRMACIONES = {
    "sí", "si", "sí!", "si!", "dale", "ok", "okey", "listo",
    "confirmo", "confirmado", "apruebo", "aprueba", "actualiza",
    "actualizar", "guardar", "guarda", "agregar", "agrega", "yes",
}

# ── NUEVO: respuestas locales para comprador, sin tocar Gemini ───────────────
# Cubren los casos más comunes (saludo, pedir ayuda, querer comprar, preguntar
# qué hacer) para que el comprador nunca se quede sin respuesta útil aunque
# Gemini/LangGraph esté caído. Si el mensaje no calza con ningún patrón,
# seguimos al flujo normal (route_message) como hasta ahora.
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
    """Intenta responder localmente a mensajes comunes de comprador sin usar
    Gemini/LangGraph. Devuelve None si no hay match, para que el caller
    siga con el flujo normal."""
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
    """Guarda un mensaje en el historial de la sesión.

    AJUSTE (18 jun): antes hacía SELECT del contexto_json actual, lo
    modificaba en memoria, y luego UPDATE — eso dejaba una ventana de
    carrera: si dos requests llegaban casi simultáneas para la misma sesión
    (reintento de webhook de Meta, o dos mensajes muy seguidos del mismo
    usuario una vez que el número del bot esté público en la web), ambas
    leían el mismo contexto_json viejo y la segunda en escribir pisaba por
    completo el historial que dejó la primera.

    Ahora delega todo (append al historial + recorte a 20 + incremento de
    contadores) a la función SQL guardar_mensaje_sesion, que hace el UPDATE
    completo dentro de una sola operación atómica de Postgres — dos llamadas
    concurrentes para el mismo sesion_id se serializan a nivel de fila en
    vez de pisarse.
    """
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
            "🌿 *ViveroOnline · Comandos*\n\n"
            "📷 *Foto* → identifico la planta y la agrego\n\n"
            "✏️ *Modificar:*\n"
            "• precio [planta] [valor]\n"
            "• stock [planta] [cantidad]\n"
            "• agotado [planta]\n"
            "• disponible [planta]\n\n"
            "📦 *Pedidos:*\n"
            "• APROBAR o RECHAZAR\n"
            "• ENVIADO (cuando despachás)\n\n"
            "Respondé *SÍ* para confirmar cambios.\n"
            "Escribí *salir* para cerrar."
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
    verify_token = os.getenv("META_WA_VERIFY_TOKEN", "")
    if mode == "subscribe" and token == verify_token:
        return Response(content=challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verify token inválido")


@router.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    body_bytes = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    if os.getenv("ENV") == "production":
        if not verify_signature(body_bytes, signature):
            raise HTTPException(status_code=403, detail="Firma inválida")
    try:
        payload = await request.json()
    except Exception:
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


# ─── Handler principal ────────────────────────────────────────────────────────

async def _handle_message(msg: dict):
    msg_type = msg.get("type")
    from_raw = msg.get("from", "")
    whatsapp = f"+{from_raw}" if from_raw and not from_raw.startswith("+") else from_raw
    if not whatsapp:
        return

    user = _find_user_by_whatsapp(whatsapp)
    if not user:
        await send_text_message(
            whatsapp,
            "👋 ¡Hola! Aún no estás registrado en ViveroOnline.\n\n"
            "Registrate gratis aquí:\nhttps://app.viveroonline.com.co/auth/ingresar",
        )
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

        # ── NUEVO: Verificar selección numerada pendiente ─────────────────────
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

        # ── Para viveristas: Copilot PRIMERO (sin Gemini) ─────────────────────
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

                # Si el copilot detectó un comando → responder directamente sin LangGraph
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

        # ── NUEVO: Para compradores: respuestas locales comunes ANTES de Gemini
        # Cubre saludo / pedir ayuda / querer comprar / preguntar qué hacer,
        # así el comprador no depende 100% de Gemini para esos casos típicos.
        if rol in ("comprador", "admin"):
            respuesta_local = _respuesta_local_comprador(lower)
            if respuesta_local:
                _save_message(sesion_id, "model", respuesta_local, agente="local_comprador")
                await send_text_message(whatsapp, respuesta_local)
                return

        # ── Fallback: LangGraph para consultas generales ──────────────────────
        try:
            result = route_message(body, ctx)
            respuesta_final = result.get("respuesta", "Hubo un problema. Intentá de nuevo.")
            agente = result.get("agente", "ai_ceo")
        except Exception as e:
            # NUEVO: el mensaje de error ya no es un callejón sin salida —
            # incluye un link directo al marketplace para que el comprador
            # pueda seguir solo aunque Gemini siga caído.
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
            # Subir foto a Storage
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
    """Viverista escribe ENVIADO -> actualiza entrega a despachado -> notifica comprador."""
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
    """Comprador escribe RECIBIDO -> actualiza entrega a entregado -> notifica viverista."""
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
    """Viverista escribe APROBAR → aprueba la cotización pendiente.

    AJUSTE (18 jun): antes este handler actualizaba cotizaciones.estado
    directamente, sin tocar sub_cotizaciones — eso rompía el flujo
    multi-vivero (una cotización con 2+ viveros podía pasar a "aceptada"
    aunque el otro vivero no hubiera respondido nada todavía). Ahora delega
    a la misma función que usa el endpoint HTTP /aprobar, que sí respeta
    sub_cotizaciones y aplica el UPDATE condicional atómico contra
    condiciones de carrera con el cron de vencimiento.
    """
    from app.routes.pedidos import aprobar_subcotizacion_vivero
    from app.services.supabase import admin as db_admin

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
        # La notificación al comprador ya la envía aprobar_subcotizacion_vivero
        # cuando todos los viveros aprobaron — no se duplica aquí.
    else:
        # parcialmente_aprobada: este vivero aprobó, pero faltan otros
        await send_text_message(
            whatsapp,
            "Tu aprobación quedó registrada\n\n"
            "Proyecto: " + nombre_proyecto + "\n"
            f"Aprobaron {resultado.get('aprobadas')} de {resultado.get('total_viveros')} viveros.\n"
            "Avisaremos al comprador cuando todos confirmen."
        )


# ─── Rechazar cotización desde WhatsApp ──────────────────────────────────────

async def _handle_rechazar(vivero_id: int, whatsapp: str, sesion_id: int, session: dict, motivo: str = ""):
    """Viverista escribe RECHAZAR → rechaza la cotización pendiente.

    AJUSTE (18 jun): antes este handler actualizaba cotizaciones.estado
    directamente sin tocar sub_cotizaciones ni buscar vivero alternativo —
    rompía el flujo multi-vivero y omitía la búsqueda de reemplazo que sí
    existe en el endpoint HTTP /rechazar. Ahora delega a la misma función.
    """
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
    # La notificación al comprador (con o sin alternativa) ya la envía
    # rechazar_subcotizacion_vivero — no se duplica aquí.
