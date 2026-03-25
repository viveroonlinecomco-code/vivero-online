"""
ViveroOnline — Agente IA (versión Vercel)
Gemini 2.5 Flash para visión y chat. Sin dependencias de Streamlit.
"""

import asyncio
import base64
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
log = logging.getLogger("vivero_agent")

GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"

VISION_PROMPT = """
Eres un agrónomo experto en plantas de la Sabana de Bogotá, Colombia (2600 msnm, clima frío).

Analiza esta imagen y devuelve ÚNICAMENTE un JSON válido con esta estructura:
{
  "nombre_comun": "nombre popular en Colombia",
  "nombre_cientifico": "Genus species",
  "descripcion": "descripción comercial de 2 frases para catálogo B2B",
  "cuidados": "instrucciones breves de riego, luz y temperatura para clima frío",
  "precio_estimado_cop": 25000,
  "stock_sugerido": "descripción de demanda en la Sabana",
  "confianza": 0.92,
  "advertencias": "toxica para mascotas si aplica, o null"
}
Solo el JSON puro, sin texto adicional, sin bloques de código markdown.
"""


@lru_cache(maxsize=1)
def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    return genai.Client(api_key=api_key)


def _parse_json(text: str) -> Optional[Dict]:
    try:
        clean = re.sub(r"```json\s*|\s*```", "", text).strip()
        return json.loads(clean)
    except Exception:
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass
    return None


def analizar_planta_con_ia(
    imagen_b64: str,
    mime_type: str,
    municipio: str = "Cajicá",
) -> Optional[Dict[str, Any]]:
    client = get_gemini_client()
    try:
        image_part = types.Part.from_bytes(
            data=base64.b64decode(imagen_b64),
            mime_type=mime_type,
        )
        config = types.GenerateContentConfig(temperature=0.2, max_output_tokens=1000)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                f"Contexto: planta para vender en vivero de {municipio}, Sabana de Bogotá.",
                image_part,
                VISION_PROMPT,
            ],
            config=config,
        )
        return _parse_json(response.text or "")
    except Exception as e:
        log.error(f"Error visión: {e}")
        return None


def chat_agente(
    mensaje: str,
    municipio: str = "Cajicá",
    historial: list = None,
) -> str:
    client = get_gemini_client()
    try:
        system = (
            f"Eres el asesor experto de ViveroOnline para viveristas de {municipio}, "
            "Sabana de Bogotá, Colombia. Ayudas con identificación de plantas, "
            "precios en COP, mercado B2B y paisajismo. "
            "Responde en español colombiano, de forma práctica y amigable. "
            "Máximo 250 palabras. Usa emojis ocasionalmente."
        )
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.7,
            max_output_tokens=600,
        )
        # Incluir historial si existe
        contents = []
        if historial:
            for msg in historial[-6:]:  # últimos 6 mensajes
                contents.append(msg)
        contents.append(mensaje)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )
        return response.text or "No pude responder en este momento."
    except Exception as e:
        return f"Error de conexión con IA: {str(e)}"
