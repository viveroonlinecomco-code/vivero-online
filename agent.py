"""
ViveroOnline — Módulo de Agente IA
===================================
Conecta el frontend Streamlit con el grafo LangGraph.
Expone dos funciones simples para la UI:
  - analizar_planta_con_ia(): visión
  - chat_agente(): conversación
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
log = logging.getLogger("vivero_agent")

# ─── CLIENTE GEMINI ───────────────────────────────────────────────────────────
@st.cache_resource
def get_gemini_client():
    try:
        api_key = st.secrets["gemini"]["api_key"]
    except Exception:
        api_key = os.environ.get("GEMINI_API_KEY", "")
    return genai.Client(api_key=api_key)

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


def _parse_json(text: str) -> Optional[Dict]:
    """Extrae JSON limpio de la respuesta de Gemini."""
    import re
    try:
        # Limpiar bloques ```json ... ```
        clean = re.sub(r"```json\s*|\s*```", "", text).strip()
        return json.loads(clean)
    except Exception:
        try:
            # Intentar extraer el primer objeto JSON del texto
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
    """
    Analiza una imagen de planta con Gemini Vision.
    Devuelve dict con nombre, precio estimado, cuidados, etc.
    """
    import base64
    client = get_gemini_client()
    try:
        image_part = types.Part.from_bytes(
            data=base64.b64decode(imagen_b64),
            mime_type=mime_type,
        )
        config = types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=1000,
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                f"Contexto: planta para vender en vivero de {municipio}, Sabana de Bogotá.",
                image_part,
                VISION_PROMPT,
            ],
            config=config,
        )
        resultado = _parse_json(response.text or "")
        if resultado:
            log.info(f"Planta identificada: {resultado.get('nombre_comun')} "
                     f"(confianza: {resultado.get('confianza', 0):.0%})")
        return resultado
    except Exception as e:
        log.error(f"Error en visión: {e}")
        st.error(f"Error analizando imagen: {e}")
        return None


def chat_agente(
    mensaje: str,
    viverista_id: str,
    municipio: str = "Cajicá",
) -> str:
    """
    Procesa un mensaje del viverista usando el grafo LangGraph completo.
    Devuelve la respuesta final consolidada del agente.
    """
    # Importar el grafo aquí para evitar inicialización al arrancar
    try:
        from graph import graph, ViveroState

        state = ViveroState(
            mensaje=mensaje,
            viverista_id=viverista_id,
            municipio=municipio,
        )

        # Ejecutar el grafo de forma síncrona desde Streamlit
        result = asyncio.run(_run_graph(graph, state))
        return result.get("respuesta_final") or "No pude generar una respuesta. Intenta de nuevo."

    except Exception as e:
        log.error(f"Error en grafo LangGraph: {e}")
        # Fallback: respuesta directa de Gemini sin el grafo completo
        return _respuesta_directa_gemini(mensaje, municipio)


async def _run_graph(graph, state) -> Dict:
    """Ejecuta el grafo LangGraph de forma asíncrona."""
    result = await graph.ainvoke(state)
    # LangGraph devuelve el estado final como dict o dataclass
    if hasattr(result, "__dict__"):
        return result.__dict__
    return result if isinstance(result, dict) else {}


def _respuesta_directa_gemini(mensaje: str, municipio: str) -> str:
    """
    Fallback: respuesta directa de Gemini cuando el grafo falla.
    Garantiza que el chat siempre funcione.
    """
    client = get_gemini_client()
    try:
        system = (
            f"Eres el asesor de ViveroOnline para viveristas de {municipio}, Sabana de Bogotá. "
            "Ayudas con identificación de plantas, precios en COP, mercado B2B y paisajismo. "
            "Responde en español colombiano, de forma práctica y amigable. Máximo 250 palabras."
        )
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.7,
            max_output_tokens=600,
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=mensaje,
            config=config,
        )
        return response.text or "No pude responder en este momento."
    except Exception as e:
        return f"Error de conexión con IA: {e}. Verifica tu GEMINI_API_KEY."
