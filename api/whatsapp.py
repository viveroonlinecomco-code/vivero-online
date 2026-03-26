"""
ViveroOnline — Bot WhatsApp para Viveristas
============================================
El viverista envía una foto de su planta por WhatsApp
y el bot responde con el análisis de IA y la confirma en el catálogo.

Flujo:
  1. Viverista envía foto por WhatsApp
  2. Twilio llama a este webhook /api/whatsapp
  3. Gemini Vision identifica la planta
  4. Se guarda en Supabase automáticamente
  5. Bot responde con nombre, precio sugerido y confirmación

Setup:
  1. Crea cuenta en twilio.com
  2. Activa WhatsApp Sandbox
  3. Configura webhook URL: https://tu-app.vercel.app/api/whatsapp
  4. Agrega TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN en Vercel
"""

import base64
import os
from io import BytesIO
from typing import Optional

import httpx
from fastapi import Form, Request
from fastapi.responses import PlainTextResponse
from PIL import Image

# ─── IMPORTAR FUNCIONES DEL PROYECTO ──────────────────────────────────────────
import sys
sys.path.append(os.path.dirname(__file__))
from agent import analizar_planta_con_ia
from db import (
    agregar_planta_catalogo,
    obtener_viverista_por_email,
    registrar_evento_flywheel,
    registrar_viverista,
)

# ─── SESIONES TEMPORALES EN MEMORIA ───────────────────────────────────────────
# Guarda el estado de la conversación por número de WhatsApp
# En producción usa Redis o Supabase para persistencia
_sesiones: dict = {}


def _twiml_response(mensaje: str) -> PlainTextResponse:
    """Genera respuesta TwiML para WhatsApp."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{mensaje}</Message>
</Response>"""
    return PlainTextResponse(xml, media_type="application/xml")


async def _descargar_imagen(url: str) -> Optional[bytes]:
    """Descarga la imagen enviada por WhatsApp vía Twilio."""
    try:
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        auth_token  = os.environ.get("TWILIO_AUTH_TOKEN", "")
        async with httpx.AsyncClient() as client:
            r = await client.get(url, auth=(account_sid, auth_token), timeout=30)
            if r.status_code == 200:
                return r.content
    except Exception as e:
        print(f"Error descargando imagen: {e}")
    return None


async def procesar_whatsapp(
    From: str = Form(...),
    Body: str = Form(""),
    NumMedia: str = Form("0"),
    MediaUrl0: str = Form(""),
    MediaContentType0: str = Form(""),
):
    """
    Webhook principal de WhatsApp.
    Recibe mensajes de Twilio y responde con análisis de IA.
    """
    telefono  = From.replace("whatsapp:", "").strip()
    mensaje   = Body.strip().lower()
    tiene_img = int(NumMedia) > 0

    sesion = _sesiones.get(telefono, {"paso": "inicio", "viverista_id": None})

    # ── PASO: Viverista no registrado ─────────────────────────────────────────
    if not sesion.get("viverista_id"):
        # Buscar por teléfono en DB
        viverista = None
        try:
            from db import get_supabase
            sb = get_supabase()
            r = sb.table("viveristas").select("*").eq("telefono", telefono).limit(1).execute()
            viverista = r.data[0] if r.data else None
        except Exception:
            pass

        if viverista:
            sesion["viverista_id"] = viverista["id"]
            sesion["municipio"]    = viverista.get("municipio", "Cajicá")
            sesion["paso"]         = "listo"
            _sesiones[telefono]    = sesion
            return _twiml_response(
                f"👋 ¡Bienvenido de vuelta, {viverista['nombre'].split()[0]}!\n\n"
                "📸 Envíame una foto de tu planta y la analizo con IA al instante.\n"
                "También puedes escribir *precio* o *ayuda*."
            )
        else:
            # Flujo de registro rápido
            if sesion["paso"] == "inicio":
                _sesiones[telefono] = {"paso": "pedir_nombre", "viverista_id": None}
                return _twiml_response(
                    "🌿 *Bienvenido a ViveroOnline*\n\n"
                    "El marketplace B2B de plantas de la Sabana de Bogotá.\n\n"
                    "Para comenzar, ¿cuál es tu nombre completo?"
                )
            elif sesion["paso"] == "pedir_nombre":
                sesion["nombre"] = Body.strip()
                sesion["paso"]   = "pedir_vivero"
                _sesiones[telefono] = sesion
                return _twiml_response(f"Perfecto, {Body.strip().split()[0]}. ¿Cómo se llama tu vivero?")

            elif sesion["paso"] == "pedir_vivero":
                sesion["nombre_vivero"] = Body.strip()
                sesion["paso"]          = "pedir_municipio"
                _sesiones[telefono]     = sesion
                return _twiml_response(
                    "¿En qué municipio estás?\n\n"
                    "Ej: Cajicá, Zipaquirá, Chía, Sopó, La Calera, Tabio..."
                )
            elif sesion["paso"] == "pedir_municipio":
                # Registrar viverista
                nuevo = registrar_viverista({
                    "nombre":        sesion.get("nombre", "Viverista"),
                    "email":         f"{telefono.replace('+','')}@whatsapp.vivero",
                    "telefono":      telefono,
                    "municipio":     Body.strip(),
                    "nombre_vivero": sesion.get("nombre_vivero", "Mi Vivero"),
                })
                if nuevo:
                    sesion["viverista_id"] = nuevo["id"]
                    sesion["municipio"]    = Body.strip()
                    sesion["paso"]         = "listo"
                    _sesiones[telefono]    = sesion
                    registrar_evento_flywheel("registro_whatsapp", nuevo["id"])
                    return _twiml_response(
                        f"✅ ¡Listo! Tu cuenta está creada.\n\n"
                        f"📸 Ahora envíame una foto de cualquier planta y te digo:\n"
                        f"• Nombre científico\n"
                        f"• Precio sugerido en COP\n"
                        f"• Cuidados para clima frío\n\n"
                        f"¡Empieza enviando tu primera foto!"
                    )
                else:
                    return _twiml_response("❌ Error creando tu cuenta. Escribe *hola* para intentar de nuevo.")

    # ── PASO: Viverista registrado — procesar imagen o mensaje ────────────────
    viverista_id = sesion["viverista_id"]
    municipio    = sesion.get("municipio", "Cajicá")

    if tiene_img and MediaUrl0:
        # ── Analizar imagen con Gemini Vision ──
        img_bytes = await _descargar_imagen(MediaUrl0)
        if not img_bytes:
            return _twiml_response("❌ No pude descargar la imagen. Intenta enviarla de nuevo.")

        # Comprimir imagen
        try:
            img = Image.open(BytesIO(img_bytes))
            if img.width > 800 or img.height > 800:
                img.thumbnail((800, 800), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()
        except Exception:
            pass

        img_b64 = base64.b64encode(img_bytes).decode()

        # Analizar con IA
        resultado = analizar_planta_con_ia(img_b64, "image/jpeg", municipio)

        if not resultado:
            return _twiml_response(
                "🤔 No pude identificar la planta claramente.\n\n"
                "Intenta con una foto más clara, con buena luz y fondo neutro."
            )

        # Guardar en catálogo
        planta = agregar_planta_catalogo(
            viverista_id=viverista_id,
            datos=resultado,
            precio_cop=resultado.get("precio_estimado_cop", 0),
            stock=1,
            imagen_bytes=img_bytes,
            imagen_nombre="whatsapp_upload.jpg",
        )
        registrar_evento_flywheel("vision_scan_whatsapp", viverista_id, {
            "planta": resultado.get("nombre_comun"),
            "confianza": resultado.get("confianza"),
        })

        # Guardar resultado en sesión para confirmación
        sesion["ultimo_resultado"] = resultado
        sesion["ultimo_planta_id"] = planta["id"] if planta else None
        _sesiones[telefono] = sesion

        nombre    = resultado.get("nombre_comun", "Planta")
        cientifico = resultado.get("nombre_cientifico", "")
        precio    = resultado.get("precio_estimado_cop", 0)
        confianza = int(resultado.get("confianza", 0) * 100)
        cuidados  = resultado.get("cuidados", "")
        advertencia = resultado.get("advertencias", "")

        respuesta = (
            f"🌿 *{nombre}*\n"
            f"_{cientifico}_\n\n"
            f"💰 Precio sugerido: *${precio:,} COP*\n"
            f"🎯 Confianza IA: {confianza}%\n\n"
            f"💧 Cuidados: {cuidados[:120]}...\n"
        )
        if advertencia and advertencia != "null":
            respuesta += f"\n⚠️ {advertencia}\n"

        if planta:
            respuesta += f"\n✅ *¡Guardada en tu catálogo!*\nYa está disponible en el marketplace."
        else:
            respuesta += f"\n⚠️ No se pudo guardar. Intenta de nuevo."

        respuesta += "\n\n📸 Envía otra foto para seguir agregando plantas."
        return _twiml_response(respuesta)

    # ── Comandos de texto ──────────────────────────────────────────────────────
    if "precio" in mensaje or "cuanto" in mensaje or "cuánto" in mensaje:
        res = sesion.get("ultimo_resultado")
        if res:
            return _twiml_response(
                f"💰 Para *{res.get('nombre_comun','la planta')}*:\n"
                f"Precio sugerido: *${res.get('precio_estimado_cop',0):,} COP*\n\n"
                f"Puedes ajustar el precio desde la app web."
            )
        return _twiml_response("Primero envíame una foto de tu planta 📸")

    if "ayuda" in mensaje or "help" in mensaje or "hola" in mensaje:
        return _twiml_response(
            "🌿 *Comandos disponibles:*\n\n"
            "📸 Envía una *foto* → identifico tu planta\n"
            "💰 Escribe *precio* → precio de la última planta\n"
            "🌐 Escribe *web* → link a tu catálogo\n"
            "📦 Escribe *catalogo* → resumen de tus plantas\n\n"
            "¿En qué te ayudo?"
        )

    if "web" in mensaje or "link" in mensaje:
        return _twiml_response(
            "🌐 Accede a tu catálogo completo en:\n"
            "https://vivero-online-zeta.vercel.app\n\n"
            "Ahí puedes ver todas tus plantas, gestionar precios y ver tus pedidos."
        )

    if "catalogo" in mensaje or "catálogo" in mensaje or "plantas" in mensaje:
        try:
            from db import obtener_catalogo
            plantas = obtener_catalogo(viverista_id)
            if not plantas:
                return _twiml_response("Tu catálogo está vacío. Envía una foto para agregar tu primera planta 📸")
            resumen = f"📦 *Tu catálogo — {len(plantas)} plantas:*\n\n"
            for p in plantas[:5]:
                resumen += f"• {p['nombre_comun']} — ${p.get('precio_cop',0):,} COP\n"
            if len(plantas) > 5:
                resumen += f"\n...y {len(plantas)-5} más en la app web."
            return _twiml_response(resumen)
        except Exception:
            return _twiml_response("Error cargando tu catálogo. Intenta en la app web.")

    # Respuesta por defecto
    return _twiml_response(
        "📸 Envíame una foto de tu planta y la analizo.\n"
        "Escribe *ayuda* para ver todos los comandos."
    )
