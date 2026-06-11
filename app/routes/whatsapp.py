"""Webhook bidireccional WhatsApp via Meta WhatsApp Cloud API.

Cuando alguien escribe al número WhatsApp del bot:
1. Meta hace POST al webhook con JSON estructurado
2. Validamos firma HMAC-SHA256 + App Secret
3. Identificamos al usuario por whatsapp_numero (tabla perfiles)
   → Si hay duplicados (admin + viverista mismo número), prioriza viverista
4. Imagen → YOLO + Gemini Vision → identificación de planta
5. Texto → LangGraph router → 8 agentes IA → respuesta
6. Copilot Layer → detecta acciones → propone CTA o ejecuta si confirmado
7. Persistimos sesión en sesiones_agente
8. Respondemos 200 OK rápido y enviamos el mensaje en background

Configuración en Meta Business Manager:
  WhatsApp > Configuración > Webhook
  Callback URL: https://vivero-online-j3gi.vercel.app/api/whatsapp/webhook
  Verify token: valor de META_WA_VERIFY_TOKEN
  Campos suscritos: messages
"""
from __future__ import annotations

import logging
import os
import time

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

# ─── Palabras de confirmación ──────────────────────────────────────────────────
CONFIRMACIONES = {
    "sí", "si", "sí!", "si!", "dale", "ok", "okey", "listo",
    "confirmo", "confirmado", "apruebo", "aprueba", "actualiza",
    "actualizar", "guardar", "guarda", "agregar", "agrega", "yes",
}

# ─── Helpers de sesión ────────────────────────────────────────────────────────

def _find_user_by_whatsapp(whatsapp: str) -> dict | None:
    """Busca el perfil del usuario por número WhatsApp.

    Regla de prioridad cuando hay múltiples perfiles con el mismo número
    (caso admin que también es viverista):
    1. Viverista con vivero_id → prioridad máxima
    2. Comprador con cliente_id
    3. Admin sin vivero_id → último recurso
    """
    db = admin()
    resp = (
        db.table("perfiles")
        .select("id, rol, vivero_id, cliente_id, whatsapp_numero, nombre_display")
        .eq("whatsapp_numero", whatsapp)
        .execute()
    )
    if not resp.data:
        return None

    perfiles = resp.data

    # Si solo hay uno, retornarlo directamente
    if len(perfiles) == 1:
        return perfiles[0]

    # Prioridad: viverista con vivero_id > comprador > admin sin vivero_id
    for p in perfiles:
        if p.get("rol") == "viverista" and p.get("vivero_id"):
            return p
    for p in perfiles:
        if p.get("rol") == "comprador" and p.get("cliente_id"):
            return p
    # Fallback: primer perfil
    return perfiles[0]


def _get_or_create_session(
    whatsapp: str,
    user_id: str | None,
    rol: str | None = None,
    vivero_id: int | None = None,
    cliente_id: int | None = None,
) -> dict:
    db = admin()
    existing = (
        db.table("sesiones_agente")
        .select("sesion_id, contexto_json, mensajes_count, accion_pendiente")
        .eq("whatsapp_numero", whatsapp)
        .eq("estado", "activa")
        .limit(1)
        .execute()
    )
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
    }).execute()
    return new.data[0] if new.data else {
        "sesion_id": None,
        "contexto_json": {"historial": []},
        "mensajes_count": 0,
        "accion_pendiente": None,
    }


def _save_message(
    sesion_id: int, role: str, content: str,
    agente: str | None = None, is_photo: bool = False,
):
    if not sesion_id:
        return
    db = admin()
    resp = (
        db.table("sesiones_agente")
        .select("contexto_json, mensajes_count, fotos_procesadas")
        .eq("sesion_id", sesion_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return
    ctx = resp.data[0].get("contexto_json") or {"historial": []}
    history = ctx.get("historial", [])
    history.append({"role": role, "content": content[:1000], "ts": int(time.time())})
    ctx["historial"] = history[-20:]

    update: dict = {
        "contexto_json": ctx,
        "mensajes_count": (resp.data[0].get("mensajes_count") or 0) + 1,
        "ultimo_mensaje": "now()",
    }
    if is_photo:
        update["fotos_procesadas"] = (resp.data[0].get("fotos_procesadas") or 0) + 1
    db.table("sesiones_agente").update(update).eq("sesion_id", sesion_id).execute()


def _set_accion_pendiente(sesion_id: int, accion: dict | None):
    """Guarda o limpia la acción pendiente de confirmación."""
    if not sesion_id:
        return
    try:
        admin().table("sesiones_agente").update({
            "accion_pendiente": accion,
        }).eq("sesion_id", sesion_id).execute()
    except Exception as e:
        logger.warning(f"No se pudo guardar accion_pendiente: {e}")


def _close_session(sesion_id: int):
    if not sesion_id:
        return
    try:
        admin().table("sesiones_agente").update({
            "estado": "cerrada",
            "fecha_cierre": "now()",
            "accion_pendiente": None,
        }).eq("sesion_id", sesion_id).execute()
    except Exception:
        pass


def _help_text(rol: str | None) -> str:
    if rol == "viverista":
        return (
            "🌿 *ViveroOnline · Comandos*\n\n"
            "📷 Enviá una *foto* → identifico la planta + la agrego a tu catálogo\n"
            "💬 Pregunta libre → agente IA responde\n\n"
            "Ejemplos:\n"
            "• Subir precio del Helecho a $15.000\n"
            "• Bajar 10 unidades del Yarumo\n"
            "• Marcar el Ficus como agotado\n"
            "• ¿Qué plantas vender en mayo?\n\n"
            "Respondé *SÍ* para confirmar cualquier cambio propuesto.\n"
            "Escribí *salir* para cerrar la sesión."
        )
    if rol == "comprador":
        return (
            "🌿 *ViveroOnline · Comandos*\n\n"
            "💬 Describí tu proyecto → te recomiendo plantas y armo la cotización\n"
            "📷 Enviá foto de inspiración → identifico la planta\n\n"
            "Escribí *salir* para cerrar."
        )
    return (
        "🌿 *ViveroOnline*\n\n"
        "Enviame fotos o preguntas sobre plantas.\n"
        "Escribí *salir* para cerrar."
    )


# ─── GET: Verificación inicial del webhook ────────────────────────────────────

@router.get("/webhook")
async def whatsapp_webhook_verify(request: Request):
    """Meta verifica el webhook con GET. Respondemos el challenge si el token coincide."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    verify_token = os.getenv("META_WA_VERIFY_TOKEN", "")
    if mode == "subscribe" and token == verify_token:
        logger.info("Webhook Meta verificado ✅")
        return Response(content=challenge or "", media_type="text/plain")

    logger.warning("Webhook Meta: verify_token inválido")
    raise HTTPException(status_code=403, detail="Verify token inválido")


# ─── POST: Webhook principal ───────────────────────────────────────────────────

@router.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Recibe eventos de Meta. Responde 200 OK rápido y procesa en background."""
    body_bytes = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")

    if os.getenv("ENV") == "production":
        if not verify_signature(body_bytes, signature):
            logger.warning("Webhook Meta: firma inválida")
            raise HTTPException(status_code=403, detail="Firma inválida")

    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}

    background_tasks.add_task(_process_payload, payload)
    return {"ok": True}


# ─── Procesador del payload en background ─────────────────────────────────────

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


async def _handle_message(msg: dict):
    """Maneja un mensaje individual entrante."""
    msg_type = msg.get("type")
    from_raw = msg.get("from", "")
    whatsapp = f"+{from_raw}" if from_raw and not from_raw.startswith("+") else from_raw
    if not whatsapp:
        return

    # 1. Buscar usuario — prioriza viverista con vivero_id
    user = _find_user_by_whatsapp(whatsapp)
    if not user:
        await send_text_message(
            whatsapp,
            "👋 ¡Hola! Aún no estás registrado en ViveroOnline.\n\n"
            "Registrate gratis aquí:\nhttps://app.viveroonline.com.co/auth/ingresar",
        )
        return

    # 2. Sesión
    session = _get_or_create_session(
        whatsapp,
        user.get("id"),
        rol=user.get("rol"),
        vivero_id=user.get("vivero_id"),
        cliente_id=user.get("cliente_id"),
    )
    sesion_id = session.get("sesion_id")
    rol = user.get("rol")
    vivero_id = user.get("vivero_id")

    # 3. Imagen → identificar planta
    if msg_type == "image":
        image_id = (msg.get("image") or {}).get("id")
        await _handle_image(image_id, user, sesion_id, whatsapp, session)
        return

    # 4. Texto → flujo principal
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

        # ── Verificar si hay acción pendiente de confirmación ─────────────────
        accion_pendiente = session.get("accion_pendiente")
        if accion_pendiente and lower in CONFIRMACIONES and rol in ("viverista", "admin"):
            await _ejecutar_accion_confirmada(
                accion_pendiente, vivero_id, user.get("id"),
                sesion_id, whatsapp
            )
            return

        # Si hay acción pendiente pero el usuario no confirma → limpiarla
        if accion_pendiente and rol in ("viverista", "admin"):
            _set_accion_pendiente(sesion_id, None)

        # ── Flujo normal: router LangGraph → copilot ──────────────────────────
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

        # Agentes LangGraph (sin cambios)
        try:
            result = route_message(body, ctx)
            respuesta_agente = result.get("respuesta", "Hubo un problema. Intentá de nuevo.")
            agente = result.get("agente", "ai_ceo")
        except Exception as e:
            respuesta_agente = "Disculpá, tuve un problema. Intentá de nuevo en un momento."
            agente = "error"
            try:
                admin().table("log_ia").insert({
                    "tipo_operacion": "whatsapp_error",
                    "input_data": {"mensaje": body[:200], "whatsapp": whatsapp},
                    "output_data": {"error": str(e)[:300]},
                }).execute()
            except Exception:
                pass

        # ── Copilot Layer (solo para viveristas) ──────────────────────────────
        respuesta_final = respuesta_agente
        if rol in ("viverista", "admin") and vivero_id:
            try:
                inventario = get_inventario_snapshot(vivero_id)
                copilot = get_copilot()
                copilot_result = copilot.procesar(
                    mensaje_usuario=body,
                    respuesta_agente=respuesta_agente,
                    ctx=ctx,
                    inventario_snapshot=inventario,
                )
                respuesta_final = copilot_result.get("respuesta", respuesta_agente)
                acciones = copilot_result.get("acciones", [])

                # Si hay acciones propuestas → guardar la primera como pendiente
                # confirmed es siempre false aquí (forzado en copilot.py)
                if acciones:
                    primera_accion = acciones[0]
                    if not primera_accion.get("needs_clarification"):
                        _set_accion_pendiente(sesion_id, primera_accion)

            except Exception as e:
                logger.warning(f"Copilot Layer error (usando respuesta original): {e}")
                respuesta_final = respuesta_agente

        # Guardar y enviar respuesta
        _save_message(sesion_id, "model", respuesta_final, agente=agente)
        if len(respuesta_final) > 4000:
            respuesta_final = respuesta_final[:3997] + "..."
        await send_text_message(whatsapp, respuesta_final)
        return

    # 5. Tipo no soportado
    await send_text_message(
        whatsapp,
        "Por ahora solo proceso texto e imágenes 🌿\n"
        "Enviame una foto de la planta o escribí tu consulta.",
    )


# ─── Ejecutar acción confirmada ────────────────────────────────────────────────

async def _ejecutar_accion_confirmada(
    accion: dict,
    vivero_id: int,
    user_id: str,
    sesion_id: int,
    whatsapp: str,
):
    """Ejecuta una acción ya confirmada por el viverista y limpia la sesión."""
    _set_accion_pendiente(sesion_id, None)

    resultado = ejecutar_accion(accion, vivero_id, user_id)

    _save_message(sesion_id, "model", resultado.mensaje, agente="executor")
    await send_text_message(whatsapp, resultado.mensaje)


# ─── Pipeline imagen → identificar planta ─────────────────────────────────────

async def _handle_image(
    image_id: str | None,
    user: dict,
    sesion_id: int,
    whatsapp: str,
    session: dict,
):
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
        yolo = get_yolo()
        image_bytes, _ = await yolo.crop_plant(image_bytes)
    except Exception:
        pass  # Si YOLO falla, seguimos con imagen original

    try:
        agent = PlantIdentifierAgent()
        analisis = agent.identify_from_bytes(image_bytes, "image/jpeg")
    except RuntimeError as e:
        error_msg = str(e)
        logger.error(f"Error identificando planta: {error_msg}")
        if "cuota_agotada" in error_msg:
            await send_text_message(
                whatsapp,
                "⏳ El servicio de IA está ocupado en este momento.\n"
                "Intentá de nuevo en unos minutos. 🌿"
            )
        else:
            await send_text_message(
                whatsapp,
                "🤔 No pude procesar esta imagen.\n\n"
                "Intentá:\n"
                "• Tomar la foto con más luz\n"
                "• Que la planta ocupe la mayor parte de la foto\n"
                "• Enviar la foto directamente (no como documento)"
            )
        return
    except Exception as e:
        logger.error(f"Error inesperado identificando planta: {e}")
        await send_text_message(
            whatsapp,
            "🤔 No pude procesar esta imagen.\n\n"
            "Intentá enviando la foto directamente desde la cámara."
        )
        return

    rol = user.get("rol")
    vivero_id = user.get("vivero_id")

    if analisis.confianza < 0.3 or analisis.nombre_comun == "No identificada":
        msg = (
            "🤔 No pude identificar esta planta con confianza.\n\n"
            "Intentá tomar la foto:\n"
            "• Con buena luz natural\n"
            "• Centrando la planta\n"
            "• Sin manos ni objetos delante"
        )
        _set_accion_pendiente(sesion_id, None)
    else:
        precio = analisis.precio_estimado_cop or 0
        precio_str = f"${precio:,} COP" if precio else "a definir"
        precio_comprador = round(precio * 1.18) if precio else 0
        altura = analisis.altura_cm_estimada or 30

        if rol in ("viverista", "admin") and vivero_id:
            # ── Subir foto a Supabase Storage antes de proponer ──────
            foto_url = None
            try:
                import uuid as _uuid
                db = admin()
                filename = f"{vivero_id}/wa_{_uuid.uuid4()}.jpg"
                logger.info(f"Subiendo foto a Storage: {filename} ({len(image_bytes)} bytes)")
                upload_resp = db.storage.from_("plantas-fotos").upload(
                    path=filename,
                    file=image_bytes,
                    file_options={"content-type": "image/jpeg"},
                )
                logger.info(f"Upload response: {upload_resp}")
                foto_url = db.storage.from_("plantas-fotos").get_public_url(filename)
                logger.info(f"Foto subida OK: {foto_url}")
            except Exception as e:
                logger.error(f"Error subiendo foto a Storage: {type(e).__name__}: {e}")

            # Para viveristas → proponer agregar al inventario
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
            # Comprador → solo informar
            msg = (
                f"🌿 *{analisis.nombre_comun}*\n"
                f"_{analisis.nombre_cientifico or ''}_\n\n"
                f"💰 Precio referencia: {precio_str}\n"
                f"☀️ Luz: {analisis.luz or 'N/D'}\n"
                f"💧 Riego: {analisis.riego or 'N/D'}\n"
                f"📏 Altura aprox: {altura} cm\n\n"
                f"✅ Confianza: {int(analisis.confianza * 100)}%\n\n"
                f"¿Querés cotizar esta planta para tu proyecto?\n"
                f"Describime cuántas necesitás y dónde."
            )

    _save_message(sesion_id, "user", "[Imagen enviada]", is_photo=True)
    _save_message(sesion_id, "model", msg, agente="plant_identifier")
    await send_text_message(whatsapp, msg)
