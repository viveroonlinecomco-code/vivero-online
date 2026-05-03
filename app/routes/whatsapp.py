"""Webhook bidireccional WhatsApp via Twilio.

Cuando un usuario envía mensaje al número de WhatsApp del bot:
1. Twilio hace POST al webhook con Body, From, MediaUrl0...
2. Validamos firma de Twilio (anti-suplantación)
3. Identificamos al usuario por su número WhatsApp (perfiles.whatsapp_numero)
4. Si hay imagen → flujo de identificación de planta
5. Si es texto → LangGraph router → 8 agentes → respuesta
6. Persistimos la sesión en sesiones_agente
7. Respondemos con TwiML (XML) que Twilio convierte en mensaje WhatsApp

Configuración en Twilio Console:
- Phone Number → WhatsApp Sender → "A Message Comes In":
  POST https://vivero-online-j3gi.vercel.app/api/whatsapp/webhook
"""
from __future__ import annotations
import time
import httpx

from fastapi import APIRouter, Form, Request, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

from app.agents import route_message, AgentContext
from app.agents.plant_identifier import PlantIdentifierAgent
from app.config import get_settings
from app.services.supabase import admin


router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


# ─────────────────── HELPERS ───────────────────

def _twiml_text(text: str) -> Response:
    """Construye respuesta TwiML con un mensaje de texto."""
    resp = MessagingResponse()
    resp.message(text)
    return Response(content=str(resp), media_type="application/xml")


def _normalize_whatsapp(twilio_from: str) -> str:
    """'whatsapp:+573001234567' → '+573001234567'"""
    return twilio_from.replace("whatsapp:", "").strip()


async def _validate_twilio_signature(request: Request, form_data: dict) -> bool:
    """Valida que el request realmente venga de Twilio."""
    s = get_settings()
    if not s.is_production:
        # En dev no validamos para facilitar testing
        return True
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    validator = RequestValidator(s.twilio_auth_token)
    return validator.validate(url, form_data, signature)


def _find_user_by_whatsapp(whatsapp: str) -> dict | None:
    """Busca el perfil del usuario por su número WhatsApp."""
    db = admin()
    resp = db.table("perfiles").select(
        "id, rol, vivero_id, cliente_id, whatsapp_numero, nombre_display"
    ).eq("whatsapp_numero", whatsapp).limit(1).execute()
    if resp.data:
        return resp.data[0]
    return None


def _get_or_create_session(whatsapp: str, user_id: str | None, rol: str | None = None,
                            vivero_id: int | None = None, cliente_id: int | None = None) -> dict:
    """Recupera o crea una sesión de agente activa para el usuario."""
    db = admin()
    # Buscar sesión activa
    existing = db.table("sesiones_agente").select(
        "sesion_id, contexto_json, mensajes_count"
    ).eq("whatsapp_numero", whatsapp).eq("estado", "activa").limit(1).execute()
    if existing.data:
        return existing.data[0]

    # Crear nueva — usando solo columnas reales de sesiones_agente
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
    return new.data[0] if new.data else {"sesion_id": None, "contexto_json": {"historial": []}, "mensajes_count": 0}


def _save_message(sesion_id: int, role: str, content: str, agente: str | None = None, is_photo: bool = False):
    """Actualiza el historial de la sesión."""
    if not sesion_id:
        return
    db = admin()
    # Recuperar historial actual
    resp = db.table("sesiones_agente").select(
        "contexto_json, mensajes_count, fotos_procesadas"
    ).eq("sesion_id", sesion_id).limit(1).execute()
    if not resp.data:
        return
    ctx = resp.data[0].get("contexto_json") or {"historial": []}
    history = ctx.get("historial", [])
    history.append({"role": role, "content": content[:1000], "ts": int(time.time())})
    history = history[-20:]  # Mantén solo los últimos 20 turnos
    ctx["historial"] = history

    update = {
        "contexto_json": ctx,
        "mensajes_count": (resp.data[0].get("mensajes_count") or 0) + 1,
        "ultimo_mensaje": "now()",
    }
    if is_photo:
        update["fotos_procesadas"] = (resp.data[0].get("fotos_procesadas") or 0) + 1

    db.table("sesiones_agente").update(update).eq("sesion_id", sesion_id).execute()


async def _download_media(url: str, account_sid: str, auth_token: str) -> bytes:
    """Descarga el media (imagen) que Twilio adjunta. Requiere autenticación."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, auth=(account_sid, auth_token))
        r.raise_for_status()
        return r.content


# ─────────────────── WEBHOOK PRINCIPAL ───────────────────

@router.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    NumMedia: str = Form("0"),
    MediaUrl0: str = Form(""),
    MediaContentType0: str = Form(""),
):
    """Webhook Twilio: recibe mensajes WhatsApp entrantes y responde."""

    # 1. Construir form_data para validar firma
    form_dict = {
        "Body": Body, "From": From, "NumMedia": NumMedia,
        "MediaUrl0": MediaUrl0, "MediaContentType0": MediaContentType0,
    }
    if not await _validate_twilio_signature(request, form_dict):
        return Response(status_code=403, content="Forbidden")

    # 2. Normalizar número
    whatsapp = _normalize_whatsapp(From)
    if not whatsapp:
        return _twiml_text("No se pudo identificar tu número. Intenta de nuevo.")

    # 3. Buscar perfil
    user = _find_user_by_whatsapp(whatsapp)
    if not user:
        return _twiml_text(
            "👋 ¡Hola! Aún no estás registrado en ViveroOnline.\n\n"
            "Regístrate gratis aquí: https://vivero-online-j3gi.vercel.app/auth/ingresar"
        )

    # 4. Crear/recuperar sesión
    session = _get_or_create_session(
        whatsapp,
        user.get("id"),
        rol=user.get("rol"),
        vivero_id=user.get("vivero_id"),
        cliente_id=user.get("cliente_id"),
    )
    sesion_id = session.get("sesion_id")

    # 5. Si hay imagen → identificar planta
    try:
        num_media = int(NumMedia or "0")
    except ValueError:
        num_media = 0

    if num_media > 0 and MediaContentType0.startswith("image/") and MediaUrl0:
        return await _handle_image(MediaUrl0, user, sesion_id, whatsapp)

    # 6. Si es texto → ruta al agente apropiado
    msg = (Body or "").strip()
    if not msg:
        return _twiml_text("Envíame un mensaje o una foto de planta para empezar 🌱")

    # Comandos especiales
    lower = msg.lower()
    if lower in ("ayuda", "help", "menu", "menú"):
        return _twiml_text(_help_text(user.get("rol")))
    if lower in ("salir", "exit", "fin"):
        _close_session(sesion_id)
        return _twiml_text("Sesión cerrada. ¡Hasta pronto! 🌿")

    # Guardar mensaje del usuario
    _save_message(sesion_id, "user", msg)

    # Construir contexto para el router
    history = session.get("contexto_json", {}).get("historial", [])
    ctx = AgentContext(
        user_id=user.get("id"),
        whatsapp=whatsapp,
        rol=user.get("rol"),
        vivero_id=user.get("vivero_id"),
        cliente_id=user.get("cliente_id"),
        historial=history[-6:],
    )

    # Llamar al router
    try:
        result = route_message(msg, ctx)
        respuesta = result.get("respuesta", "Hubo un problema. Intenta de nuevo.")
        agente = result.get("agente", "ai_ceo")
    except Exception as e:
        respuesta = f"Disculpa, tuve un problema procesando tu mensaje. Intenta de nuevo en un momento."
        agente = "error"
        # Log el error sin interrumpir
        try:
            admin().table("log_ia").insert({
                "tipo_operacion": "whatsapp_error",
                "input_data": {"mensaje": msg[:200], "whatsapp": whatsapp},
                "output_data": {"error": str(e)[:300]},
            }).execute()
        except Exception:
            pass

    # Guardar respuesta del agente
    _save_message(sesion_id, "model", respuesta, agente=agente)

    # WhatsApp tiene límite de 1600 caracteres por mensaje
    if len(respuesta) > 1500:
        respuesta = respuesta[:1497] + "..."

    return _twiml_text(respuesta)


# ─────────────────── IMAGEN → IDENTIFICAR PLANTA ───────────────────

async def _handle_image(media_url: str, user: dict, sesion_id: int, whatsapp: str):
    """Descarga imagen, ejecuta YOLO+Gemini, retorna identificación."""
    s = get_settings()

    try:
        image_bytes = await _download_media(media_url, s.twilio_account_sid, s.twilio_auth_token)
    except Exception as e:
        return _twiml_text(f"No pude descargar tu imagen: {str(e)[:80]}. Intenta enviarla de nuevo.")

    # YOLO preprocessing
    from app.services.yolo import get_yolo
    yolo = get_yolo()
    cropped, yolo_meta = await yolo.crop_plant(image_bytes)

    # Gemini Vision
    agent = PlantIdentifierAgent()
    analisis = agent.identify_from_bytes(cropped, "image/jpeg")

    if analisis.confianza < 0.3 or analisis.nombre_comun == "No identificada":
        msg = (
            "🤔 No pude identificar esta planta con confianza.\n\n"
            "Intenta tomar una foto:\n"
            "• Con buena luz natural\n"
            "• Centrando la planta\n"
            "• Sin manos ni objetos delante"
        )
    else:
        precio_str = f"${analisis.precio_estimado_cop:,} COP".replace(",", ".") if analisis.precio_estimado_cop else "Consultar"
        cuidados = analisis.cuidados or "Cuidados estándar"
        msg = (
            f"🌿 *{analisis.nombre_comun}*\n"
            f"_{analisis.nombre_cientifico or ''}_\n\n"
            f"💰 Precio sugerido: {precio_str}\n"
            f"☀️ Luz: {analisis.luz or 'N/D'}\n"
            f"💧 Riego: {analisis.riego or 'N/D'}\n"
            f"📏 Altura aprox: {analisis.altura_cm_estimada or 'N/D'} cm\n\n"
            f"📋 {cuidados}\n\n"
            f"✅ Confianza: {int(analisis.confianza * 100)}%\n\n"
        )
        if user.get("rol") == "viverista":
            msg += "Ve a la app para guardarla en tu inventario:\nhttps://vivero-online-j3gi.vercel.app/viverista"

    _save_message(sesion_id, "user", "[Imagen enviada]", is_photo=True)
    _save_message(sesion_id, "model", msg, agente="plant_identifier")

    return _twiml_text(msg)


# ─────────────────── HELPERS DE TEXTO ───────────────────

def _help_text(rol: str | None) -> str:
    if rol == "viverista":
        return (
            "🌿 *ViveroOnline · Comandos*\n\n"
            "📷 Envía una *foto* → identifico la planta + precio sugerido\n"
            "💬 Pregunta libre → un agente IA te responde\n"
            "Ejemplos:\n"
            "• ¿Qué plantas vender en mayo?\n"
            "• Sugerencias de precio\n"
            "• ¿Cómo subo mi inventario?\n\n"
            "Escribe *salir* para cerrar la sesión."
        )
    if rol == "comprador":
        return (
            "🌿 *ViveroOnline · Comandos*\n\n"
            "💬 Pregúntame por proyectos de paisajismo:\n"
            "• Plantas para zona seca con poco riego\n"
            "• ¿Cuántas matas para un seto de 20m?\n"
            "• Precio por mayor de Ficus Lyrata\n\n"
            "📷 Envía foto de inspiración → te ayudo a identificar la planta\n\n"
            "Escribe *salir* para cerrar la sesión."
        )
    return (
        "🌿 *ViveroOnline*\n\n"
        "Envíame fotos o pregúntame lo que quieras del marketplace.\n"
        "Escribe *salir* para cerrar."
    )


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


# ─────────────────── HEALTH CHECK ───────────────────

@router.get("/webhook")
async def webhook_health():
    """GET handler para que Twilio pueda verificar el endpoint en setup."""
    return {"ok": True, "service": "whatsapp_webhook", "ready": True}
