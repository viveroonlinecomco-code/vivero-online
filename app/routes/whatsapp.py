"""Webhook bidireccional WhatsApp via Meta WhatsApp Cloud API.

Cuando alguien escribe al número WhatsApp del bot:
1. Meta hace POST al webhook con JSON estructurado
2. Validamos firma HMAC-SHA256 + App Secret
3. Identificamos al usuario por whatsapp_numero (tabla perfiles)
4. Imagen → YOLO + Gemini Vision → identificación de planta
5. Texto → LangGraph router → 8 agentes IA → respuesta
6. Persistimos sesión en sesiones_agente
7. Respondemos 200 OK rápido y enviamos el mensaje en background
   (Meta Cloud API NO usa respuesta inline — hay que hacer POST aparte)

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
from app.services.supabase import admin
from app.services.whatsapp_meta import (
    download_media_bytes,
    send_text_message,
    verify_signature,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


# ─── Helpers de sesión (sin cambios vs versión Twilio) ────────────────────────

def _find_user_by_whatsapp(whatsapp: str) -> dict | None:
    db = admin()
    resp = (
        db.table("perfiles")
        .select("id, rol, vivero_id, cliente_id, whatsapp_numero, nombre_display")
        .eq("whatsapp_numero", whatsapp)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


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
        .select("sesion_id, contexto_json, mensajes_count")
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
    }).execute()
    return new.data[0] if new.data else {
        "sesion_id": None,
        "contexto_json": {"historial": []},
        "mensajes_count": 0,
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


def _close_session(sesion_id: int):
    if not sesion_id:
        return
    try:
        admin().table("sesiones_agente").update({
            "estado": "cerrada",
            "fecha_cierre": "now()",
        }).eq("sesion_id", sesion_id).execute()
    except Exception:
        pass


def _help_text(rol: str | None) -> str:
    if rol == "viverista":
        return (
            "🌿 *ViveroOnline · Comandos*\n\n"
            "📷 Enviá una *foto* → identifico la planta + precio sugerido\n"
            "💬 Pregunta libre → agente IA responde\n\n"
            "Ejemplos:\n"
            "• ¿Qué plantas vender en mayo?\n"
            "• Bajar 10 unidades del Yarumo\n"
            "• Subir precio del Helecho a $15.000\n\n"
            "Escribí *salir* para cerrar la sesión."
        )
    if rol == "comprador":
        return (
            "🌿 *ViveroOnline · Comandos*\n\n"
            "💬 Consultá sobre plantas y paisajismo\n"
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
                    continue  # Ignorar delivered/read
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

    # 1. Buscar usuario
    user = _find_user_by_whatsapp(whatsapp)
    if not user:
        await send_text_message(
            whatsapp,
            "👋 ¡Hola! Aún no estás registrado en ViveroOnline.\n\n"
            "Registrate gratis aquí:\nhttps://vivero-online-j3gi.vercel.app/auth/ingresar",
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

    # 3. Imagen → identificar planta
    if msg_type == "image":
        image_id = (msg.get("image") or {}).get("id")
        await _handle_image(image_id, user, sesion_id, whatsapp)
        return

    # 4. Texto → router de agentes
    if msg_type == "text":
        body = (msg.get("text") or {}).get("body", "").strip()
        if not body:
            return

        lower = body.lower()
        if lower in ("ayuda", "help", "menu", "menú"):
            await send_text_message(whatsapp, _help_text(user.get("rol")))
            return
        if lower in ("salir", "exit", "fin"):
            _close_session(sesion_id)
            await send_text_message(whatsapp, "Sesión cerrada. ¡Hasta pronto! 🌿")
            return

        _save_message(sesion_id, "user", body)
        history = session.get("contexto_json", {}).get("historial", [])
        ctx = AgentContext(
            user_id=user.get("id"),
            whatsapp=whatsapp,
            rol=user.get("rol"),
            vivero_id=user.get("vivero_id"),
            cliente_id=user.get("cliente_id"),
            historial=history[-6:],
        )

        try:
            result = route_message(body, ctx)
            respuesta = result.get("respuesta", "Hubo un problema. Intentá de nuevo.")
            agente = result.get("agente", "ai_ceo")
        except Exception as e:
            respuesta = "Disculpá, tuve un problema. Intentá de nuevo en un momento."
            agente = "error"
            try:
                admin().table("log_ia").insert({
                    "tipo_operacion": "whatsapp_error",
                    "input_data": {"mensaje": body[:200], "whatsapp": whatsapp},
                    "output_data": {"error": str(e)[:300]},
                }).execute()
            except Exception:
                pass

        _save_message(sesion_id, "model", respuesta, agente=agente)
        if len(respuesta) > 4000:
            respuesta = respuesta[:3997] + "..."
        await send_text_message(whatsapp, respuesta)
        return

    # 5. Tipo no soportado
    await send_text_message(
        whatsapp,
        "Por ahora solo proceso texto e imágenes 🌿\n"
        "Enviame una foto de la planta o escribí tu consulta.",
    )


# ─── Pipeline imagen → identificar planta ─────────────────────────────────────

async def _handle_image(
    image_id: str | None, user: dict, sesion_id: int, whatsapp: str
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
        pass  # Si YOLO no está disponible, seguimos con la imagen original

    try:
        agent = PlantIdentifierAgent()
        analisis = agent.identify_from_bytes(image_bytes, "image/jpeg")
    except Exception as e:
        await send_text_message(whatsapp, "Error procesando la imagen. Intentá de nuevo.")
        logger.error(f"Error identificando planta: {e}")
        return

    if analisis.confianza < 0.3 or analisis.nombre_comun == "No identificada":
        msg = (
            "🤔 No pude identificar esta planta con confianza.\n\n"
            "Intentá tomar la foto:\n"
            "• Con buena luz natural\n"
            "• Centrando la planta\n"
            "• Sin manos ni objetos delante"
        )
    else:
        precio_str = (
            f"${analisis.precio_estimado_cop:,} COP".replace(",", ".")
            if analisis.precio_estimado_cop
            else "Consultar"
        )
        msg = (
            f"🌿 *{analisis.nombre_comun}*\n"
            f"_{analisis.nombre_cientifico or ''}_\n\n"
            f"💰 Precio sugerido: {precio_str}\n"
            f"☀️ Luz: {analisis.luz or 'N/D'}\n"
            f"💧 Riego: {analisis.riego or 'N/D'}\n"
            f"📏 Altura aprox: {analisis.altura_cm_estimada or 'N/D'} cm\n\n"
            f"✅ Confianza: {int(analisis.confianza * 100)}%\n\n"
        )
        if user.get("rol") == "viverista":
            msg += (
                "¿Querés guardarla en tu inventario?\n"
                "Respondé *sí* o entrá a la app:\n"
                "https://vivero-online-j3gi.vercel.app/viverista"
            )

    _save_message(sesion_id, "user", "[Imagen enviada]", is_photo=True)
    _save_message(sesion_id, "model", msg, agente="plant_identifier")
    await send_text_message(whatsapp, msg)
