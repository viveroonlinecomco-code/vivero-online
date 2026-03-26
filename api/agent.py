"""
ViveroOnline — Agente IA con Pipeline YOLO-11 + Gemini
=======================================================
Pipeline: YOLO-11 detecta/recorta → Gemini 2.5 Flash identifica
"""

import base64
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
log = logging.getLogger("vivero_agent")
GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"

VISION_PROMPT = """
Eres un agrónomo experto en plantas de la Sabana de Bogotá, Colombia (2600 msnm, clima frío).
Analiza esta imagen y devuelve ÚNICAMENTE un JSON válido:
{
  "nombre_comun": "nombre popular en Colombia",
  "nombre_cientifico": "Genus species",
  "descripcion": "descripción comercial de 2 frases para catálogo B2B",
  "cuidados": "instrucciones breves riego, luz y temperatura clima frío",
  "precio_estimado_cop": 25000,
  "stock_sugerido": "descripción de demanda en la Sabana",
  "confianza": 0.92,
  "advertencias": "toxica para mascotas o null"
}
Solo JSON puro. Sin texto adicional. Sin bloques markdown.
"""

@lru_cache(maxsize=1)
def get_gemini_client():
    return genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

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

async def analizar_planta_pipeline(imagen_bytes: bytes, municipio: str = "Cajicá") -> Optional[Dict]:
    """Pipeline completo: YOLO-11 recorta → Gemini identifica."""
    from yolo_detector import comprimir_imagen, detectar_y_recortar_planta
    imagen_bytes = comprimir_imagen(imagen_bytes)
    imagen_procesada, info_yolo = await detectar_y_recortar_planta(imagen_bytes)
    log.info(f"YOLO: activo={info_yolo['yolo_activo']} conf={info_yolo['confianza']}")
    img_b64 = base64.b64encode(imagen_procesada).decode()
    resultado = analizar_planta_con_ia(img_b64, "image/jpeg", municipio)
    if resultado:
        resultado["yolo_deteccion"] = info_yolo
    return resultado

def analizar_planta_con_ia(imagen_b64: str, mime_type: str, municipio: str = "Cajicá") -> Optional[Dict]:
    """Gemini 2.5 Flash identifica la planta."""
    client = get_gemini_client()
    try:
        image_part = types.Part.from_bytes(
            data=base64.b64decode(imagen_b64), mime_type=mime_type)
        config = types.GenerateContentConfig(temperature=0.2, max_output_tokens=1000)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[f"Planta de {municipio}, Sabana de Bogotá.", image_part, VISION_PROMPT],
            config=config,
        )
        return _parse_json(response.text or "")
    except Exception as e:
        log.error(f"Error Gemini Vision: {e}")
        return None

def chat_agente(mensaje: str, municipio: str = "Cajicá", historial: List[str] = None) -> str:
    """Chat con el asesor IA de ViveroOnline."""
    client = get_gemini_client()
    try:
        system = (
            f"Eres asesor experto de ViveroOnline para viveristas de {municipio}, "
            "Sabana de Bogotá. Ayudas con plantas, precios COP, mercado B2B, "
            "paisajismo, clima frío 2600 msnm. Español colombiano. Máx 250 palabras."
        )
        config = types.GenerateContentConfig(
            system_instruction=system, temperature=0.7, max_output_tokens=600)
        contents = list(historial[-6:]) if historial else []
        contents.append(mensaje)
        response = client.models.generate_content(
            model=GEMINI_MODEL, contents=contents, config=config)
        return response.text or "No pude responder."
    except Exception as e:
        return f"Error IA: {str(e)}"
