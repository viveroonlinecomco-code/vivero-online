"""
ViveroOnline — Grafo Multi-Agente (LangGraph)
=============================================
Arquitectura de 8 agentes en cadena para el marketplace AgTech B2B.
SDK: google-genai >= 1.0 (compatible con protobuf >= 6 — sin conflicto)

Flujo:
  AI_CEO → AI_Researcher → AI_Architect → AI_Startup_Builder
        → Plant_Identifier → Inventory_Builder
        → Landscape_Advisor → Demand_Predictor → END

Uso en Studio:
  langgraph dev --config graph_config.json

Uso en producción:
  Desplegado vía LangGraph Platform / Cloud Run
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

load_dotenv()

log = logging.getLogger("vivero_graph")

# ─── CLIENTE GEMINI (google-genai SDK unificado) ─────────────────────────────
# Este SDK es compatible con protobuf >= 6 — sin conflicto con LangGraph
_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"


# ─── CONTEXTO (configurable por asistente en LangGraph Studio) ───────────────
class Context(TypedDict, total=False):
    """Parámetros configurables por asistente o por invocación.

    En LangGraph Studio: Settings → Assistants → Edit Context.
    """
    viverista_id: str       # UUID del viverista autenticado
    municipio: str          # municipio en la Sabana de Bogotá
    modo: str               # 'catalogo' | 'busqueda' | 'consejo' | 'prediccion'
    imagen_base64: str      # imagen de planta en base64 para Plant_Identifier
    imagen_mime: str        # 'image/jpeg' | 'image/png'


# ─── ESTADO DEL GRAFO ────────────────────────────────────────────────────────
@dataclass
class ViveroState:
    """Estado compartido entre todos los agentes del grafo.

    Cada agente puede leer el estado completo y actualizar sus campos.
    """
    # Entrada del usuario
    mensaje: str = ""
    viverista_id: Optional[str] = None
    municipio: str = "Cajicá"

    # Imagen para análisis de visión (opcional)
    imagen_base64: Optional[str] = None
    imagen_mime: str = "image/jpeg"

    # Salidas de cada agente (se acumulan)
    estrategia_ceo: str = ""
    investigacion: str = ""
    arquitectura: str = ""
    plan_startup: str = ""

    # Resultados del equipo operativo
    planta_identificada: Optional[Dict[str, Any]] = None
    inventario: List[Dict[str, Any]] = field(default_factory=list)
    consejos_paisajismo: str = ""
    prediccion_demanda: str = ""

    # Respuesta final consolidada para el usuario
    respuesta_final: str = ""

    # Errores no fatales (se loguean pero no detienen el flujo)
    errores: List[str] = field(default_factory=list)


# ─── HELPERS ─────────────────────────────────────────────────────────────────
def _ask_gemini(prompt: str, system: str = "") -> str:
    """Llama a Gemini con texto. Devuelve string vacío si hay error."""
    try:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.7,
            max_output_tokens=1024,
        )
        response = _client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=config,
        )
        return response.text or ""
    except Exception as e:
        log.warning(f"Gemini text error: {e}")
        return f"[Error Gemini: {e}]"


def _ask_gemini_vision(prompt: str, image_b64: str, mime: str = "image/jpeg") -> str:
    """Llama a Gemini con imagen + texto. Devuelve string vacío si hay error."""
    try:
        image_part = types.Part.from_bytes(
            data=base64.b64decode(image_b64),
            mime_type=mime,
        )
        config = types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=1500,
        )
        response = _client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, image_part],
            config=config,
        )
        return response.text or ""
    except Exception as e:
        log.warning(f"Gemini vision error: {e}")
        return f"[Error Gemini Vision: {e}]"


def _parse_json_safe(text: str) -> Optional[Dict]:
    """Extrae JSON de la respuesta de Gemini (puede venir con ```json)."""
    try:
        clean = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(clean)
    except Exception:
        return None


# ─── NODO 1: AI_CEO ──────────────────────────────────────────────────────────
async def ai_ceo(state: ViveroState) -> Dict[str, Any]:
    """Dirige la junta. Decide qué área del negocio atender según el mensaje."""
    system = (
        "Eres el CEO de ViveroOnline, el marketplace AgTech B2B líder de la Sabana de Bogotá. "
        "Tu rol es analizar la solicitud del viverista y determinar la estrategia de respuesta. "
        "Sé conciso y ejecutivo. Máximo 3 oraciones."
    )
    prompt = (
        f"Solicitud del viverista en {state.municipio}: '{state.mensaje}'\n"
        f"¿Hay imagen adjunta?: {'Sí' if state.imagen_base64 else 'No'}\n\n"
        "Define en 1-2 oraciones: ¿cuál es el objetivo principal de esta solicitud "
        "y qué valor generamos para el viverista?"
    )
    estrategia = _ask_gemini(prompt, system)
    return {"estrategia_ceo": estrategia}


# ─── NODO 2: AI_Researcher ───────────────────────────────────────────────────
async def ai_researcher(state: ViveroState) -> Dict[str, Any]:
    """Investiga el contexto del mercado de plantas en la Sabana de Bogotá."""
    system = (
        "Eres el Investigador de Mercado de ViveroOnline. "
        "Conoces profundamente el mercado de plantas ornamentales y agrícolas "
        "de la Sabana de Bogotá (altitud 2550-2650 msnm, clima frío). "
        "Basas tus análisis en contexto local colombiano."
    )
    prompt = (
        f"Estrategia del CEO: {state.estrategia_ceo}\n"
        f"Solicitud original: '{state.mensaje}'\n"
        f"Municipio: {state.municipio}\n\n"
        "Proporciona contexto de mercado relevante: "
        "¿qué plantas tienen demanda en esta zona?, "
        "¿cuáles son los precios típicos en COP?, "
        "¿hay alguna tendencia estacional relevante ahora?"
    )
    investigacion = _ask_gemini(prompt, system)
    return {"investigacion": investigacion}


# ─── NODO 3: AI_Architect ────────────────────────────────────────────────────
async def ai_architect(state: ViveroState) -> Dict[str, Any]:
    """Define la arquitectura técnica de la respuesta (qué herramientas usar)."""
    system = (
        "Eres el Arquitecto de Soluciones de ViveroOnline. "
        "Decides qué capacidades técnicas activar: "
        "visión por computadora, búsqueda en catálogo, predicción de demanda, "
        "o asesoría de paisajismo. Sé preciso y técnico."
    )
    prompt = (
        f"Investigación de mercado: {state.investigacion}\n"
        f"¿Hay imagen?: {'Sí — analizar con Gemini Vision' if state.imagen_base64 else 'No'}\n\n"
        "Define en 2-3 puntos cuáles módulos técnicos activar y por qué."
    )
    arquitectura = _ask_gemini(prompt, system)
    return {"arquitectura": arquitectura}


# ─── NODO 4: AI_Startup_Builder ──────────────────────────────────────────────
async def ai_startup_builder(state: ViveroState) -> Dict[str, Any]:
    """Prepara el plan de acción concreto para el equipo operativo."""
    system = (
        "Eres el Director de Producto de ViveroOnline. "
        "Traduces la estrategia en acciones concretas para el equipo operativo. "
        "Eres práctico y orientado a resultados para viveristas colombianos."
    )
    prompt = (
        f"Arquitectura técnica: {state.arquitectura}\n"
        f"Solicitud del viverista: '{state.mensaje}'\n\n"
        "Define el plan de acción para este caso específico en máximo 3 pasos."
    )
    plan = _ask_gemini(prompt, system)
    return {"plan_startup": plan}


# ─── NODO 5: Plant_Identifier ────────────────────────────────────────────────
async def plant_identifier(state: ViveroState) -> Dict[str, Any]:
    """Identifica la planta. Usa Gemini Vision si hay imagen, sino texto."""
    if state.imagen_base64:
        # Ruta de visión: análisis real con Gemini multimodal
        prompt = """
Eres un agrónomo experto en plantas de la Sabana de Bogotá, Colombia (2600 msnm, clima frío).

Analiza esta imagen y devuelve ÚNICAMENTE un JSON con esta estructura exacta:
{
  "nombre_comun": "nombre popular en Colombia",
  "nombre_cientifico": "Genus species",
  "descripcion": "descripción comercial de 2 frases para catálogo B2B",
  "cuidados": "riego semanal, luz indirecta, temperatura 10-20°C (adapta según la planta)",
  "precio_estimado_cop": 25000,
  "stock_sugerido": "alta demanda en viveros de Sabana",
  "confianza": 0.92,
  "advertencias": "toxica para mascotas" o null
}
Solo el JSON, sin texto adicional ni bloques de código.
"""
        raw = _ask_gemini_vision(prompt, state.imagen_base64, state.imagen_mime)
        planta = _parse_json_safe(raw)
        if not planta:
            # Fallback: al menos devolver el texto
            planta = {"nombre_comun": "No identificada", "raw_response": raw, "confianza": 0.0}
    else:
        # Ruta de texto: identificar por descripción en el mensaje
        prompt = (
            f"El viverista menciona: '{state.mensaje}'\n"
            f"Municipio: {state.municipio}\n\n"
            "Si menciona una planta, proporciona su ficha comercial en JSON con los campos: "
            "nombre_comun, nombre_cientifico, descripcion, cuidados, precio_estimado_cop, confianza. "
            "Si no menciona ninguna planta, devuelve: {\"nombre_comun\": \"No especificada\", \"confianza\": 0}"
        )
        raw = _ask_gemini(prompt)
        planta = _parse_json_safe(raw) or {"nombre_comun": "No especificada", "raw": raw}

    return {"planta_identificada": planta}


# ─── NODO 6: Inventory_Builder ───────────────────────────────────────────────
async def inventory_builder(state: ViveroState) -> Dict[str, Any]:
    """Construye o actualiza el inventario del catálogo del viverista."""
    planta = state.planta_identificada or {}
    nombre = planta.get("nombre_comun", "planta no identificada")

    system = (
        "Eres el Gestor de Inventario de ViveroOnline. "
        "Ayudas a los viveristas de la Sabana de Bogotá a organizar su catálogo B2B. "
        "Piensas en SKUs, rotación de stock y precios competitivos en COP."
    )
    prompt = (
        f"Planta identificada: {json.dumps(planta, ensure_ascii=False)}\n"
        f"Viverista en: {state.municipio}\n\n"
        "Genera 3 recomendaciones concretas para el manejo de inventario de esta planta: "
        "precio sugerido en COP, cantidad mínima para catálogo B2B, y estrategia de reposición."
    )
    recomendaciones = _ask_gemini(prompt, system)

    # Simula entrada al catálogo (en producción: INSERT en Supabase)
    item_catalogo = {
        "nombre": nombre,
        "precio_cop": planta.get("precio_estimado_cop", 0),
        "municipio": state.municipio,
        "viverista_id": state.viverista_id,
        "recomendaciones": recomendaciones,
    }
    return {"inventario": [item_catalogo]}


# ─── NODO 7: Landscape_Advisor ───────────────────────────────────────────────
async def landscape_advisor(state: ViveroState) -> Dict[str, Any]:
    """Asesora sobre usos de paisajismo y jardinería para maximizar el valor."""
    planta = state.planta_identificada or {}
    nombre = planta.get("nombre_comun", "esta planta")

    system = (
        "Eres el Asesor de Paisajismo de ViveroOnline, especialista en jardines "
        "de clima frío de la Sabana de Bogotá. Conectas a viveristas con proyectos "
        "de arquitectura paisajística, conjuntos residenciales y espacios corporativos."
    )
    prompt = (
        f"Planta: {nombre} — {planta.get('nombre_cientifico', '')}\n"
        f"Descripción: {planta.get('descripcion', '')}\n\n"
        "Sugiere 2-3 aplicaciones de paisajismo específicas para la Sabana de Bogotá "
        "donde esta planta genera valor premium. Menciona tipos de clientes B2B ideales."
    )
    consejos = _ask_gemini(prompt, system)
    return {"consejos_paisajismo": consejos}


# ─── NODO 8: Demand_Predictor ────────────────────────────────────────────────
async def demand_predictor(state: ViveroState) -> Dict[str, Any]:
    """Predice demanda y genera la respuesta final consolidada para el viverista."""
    planta = state.planta_identificada or {}
    nombre = planta.get("nombre_comun", "la planta")

    system = (
        "Eres el Analista de Demanda de ViveroOnline. "
        "Predices tendencias del mercado de plantas en Colombia y generas "
        "la respuesta final que verá el viverista — clara, útil y accionable."
    )

    # Primero: predicción de demanda
    prompt_demanda = (
        f"Planta: {nombre}\n"
        f"Municipio: {state.municipio}\n"
        f"Consejos de paisajismo: {state.consejos_paisajismo[:300]}\n\n"
        "Predice la demanda para los próximos 3 meses. "
        "¿Alta, media o baja? ¿Por qué? ¿Hay festividades o temporadas relevantes en Colombia?"
    )
    prediccion = _ask_gemini(prompt_demanda, system)

    # Luego: respuesta final consolidada (la que ve el usuario)
    prompt_final = f"""
Consolida toda la información en una respuesta amigable para un viverista colombiano.

DATOS DISPONIBLES:
- Planta identificada: {json.dumps(planta, ensure_ascii=False, indent=2)}
- Inventario: {json.dumps(state.inventario, ensure_ascii=False)}
- Consejos de paisajismo: {state.consejos_paisajismo[:400]}
- Predicción de demanda: {prediccion[:300]}
- Solicitud original: "{state.mensaje}"

FORMATO DE RESPUESTA (usa emojis, sé cercano y profesional):
1. 🌿 **Identificación**: nombre y precio estimado
2. 📦 **Tu catálogo**: recomendación de precio y stock
3. 🏡 **Oportunidad de negocio**: usos en paisajismo
4. 📈 **Predicción de demanda**: tendencia próximos meses
5. ✅ **Próximo paso**: una acción concreta que puede tomar hoy

Responde en español colombiano, máximo 300 palabras.
"""
    respuesta_final = _ask_gemini(prompt_final, system)

    return {
        "prediccion_demanda": prediccion,
        "respuesta_final": respuesta_final,
    }


# ─── CONSTRUCCIÓN DEL GRAFO ───────────────────────────────────────────────────
def build_graph() -> StateGraph:
    """Ensambla el grafo multi-agente de ViveroOnline."""
    builder = StateGraph(ViveroState, context_schema=Context)

    # A. Registrar los 8 nodos
    builder.add_node("AI_CEO",              ai_ceo)
    builder.add_node("AI_Researcher",       ai_researcher)
    builder.add_node("AI_Architect",        ai_architect)
    builder.add_node("AI_Startup_Builder",  ai_startup_builder)
    builder.add_node("Plant_Identifier",    plant_identifier)
    builder.add_node("Inventory_Builder",   inventory_builder)
    builder.add_node("Landscape_Advisor",   landscape_advisor)
    builder.add_node("Demand_Predictor",    demand_predictor)

    # B. Orden de la Junta Directiva
    builder.add_edge(START,               "AI_CEO")
    builder.add_edge("AI_CEO",            "AI_Researcher")
    builder.add_edge("AI_Researcher",     "AI_Architect")
    builder.add_edge("AI_Architect",      "AI_Startup_Builder")

    # C. El Builder le pasa el trabajo al equipo operativo
    builder.add_edge("AI_Startup_Builder", "Plant_Identifier")

    # D. Orden de los Especialistas
    builder.add_edge("Plant_Identifier",  "Inventory_Builder")
    builder.add_edge("Inventory_Builder", "Landscape_Advisor")
    builder.add_edge("Landscape_Advisor", "Demand_Predictor")
    builder.add_edge("Demand_Predictor",  END)

    return builder.compile(name="ViveroOnline AgTech B2B")


# ─── EXPORTAR EL GRAFO ────────────────────────────────────────────────────────
# LangGraph Studio carga esta variable al hacer: langgraph dev --config graph_config.json
graph = build_graph()
