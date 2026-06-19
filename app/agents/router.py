"""LangGraph router: clasifica intención → dispacha al agente correcto.

Arquitectura:
    (ENTRY) → classify_intent → dispatch → agent.run() → END

La clasificación usa Gemini Flash en modo estructurado para devolver
el `agent_name`. Si falla, cae en AI_CEO como fallback.

AJUSTE (18 jun): se agrega detección de intentos de extracción de
información confidencial (tecnología, datos de usuarios, márgenes
internos) antes de llegar a cualquier agente. Si se detecta, se
responde directamente sin invocar ningún agente.
"""
from __future__ import annotations
import json
import logging
import re
from typing import TypedDict

from langgraph.graph import StateGraph, END

from app.services.gemini import get_gemini
from app.agents.base import AgentContext
from app.agents.ceo import AICeoAgent
from app.agents.researcher import AIResearcherAgent
from app.agents.architect import AIArchitectAgent
from app.agents.startup_builder import StartupBuilderAgent
from app.agents.plant_identifier import PlantIdentifierAgent
from app.agents.inventory_builder import InventoryBuilderAgent
from app.agents.landscape_advisor import LandscapeAdvisorAgent
from app.agents.demand_predictor import DemandPredictorAgent

logger = logging.getLogger(__name__)


# ─────────────────── REGISTRO DE AGENTES ───────────────────

_AGENTS: dict[str, type] = {
    "ai_ceo": AICeoAgent,
    "ai_researcher": AIResearcherAgent,
    "ai_architect": AIArchitectAgent,
    "startup_builder": StartupBuilderAgent,
    "plant_identifier": PlantIdentifierAgent,
    "inventory_builder": InventoryBuilderAgent,
    "landscape_advisor": LandscapeAdvisorAgent,
    "demand_predictor": DemandPredictorAgent,
}


# ─────────────────── FILTRO DE CONFIDENCIALIDAD ───────────────────
# AJUSTE (18 jun): detecta patrones de extracción de información
# confidencial ANTES de clasificar o despachar a cualquier agente.
# Si hay match, se responde directamente sin invocar Gemini ni ningún
# agente — así no hay riesgo de que el modelo "se convenza" de revelar.

_PATRONES_EXTRACCION = [
    # Tecnología interna
    re.compile(r"(qu[eé]\s+(modelo|ia|inteligencia|llm|gpt|gemini|claude|openai|stack|tecnolog|framework|langchain|langgraph|yolo|base\s+de\s+datos|supabase|vercel|python|fastapi))", re.IGNORECASE),
    re.compile(r"(c[oó]mo\s+(funciona|est[aá]\s+hecho|fue\s+construido|programaron|desarrollaron)\s+(el\s+)?(bot|sistema|plataforma|app))", re.IGNORECASE),
    re.compile(r"(eres\s+(chatgpt|gpt|gemini|claude|llama|openai|anthropic|una?\s+ia\s+de))", re.IGNORECASE),
    re.compile(r"(ignora|olvida|deja\s+de\s+lado).{0,30}(instrucciones|reglas|restricciones|sistema)", re.IGNORECASE),
    re.compile(r"(act[uú]a\s+como|pretende\s+ser|eres\s+ahora|nuevo\s+rol|desde\s+ahora\s+eres)", re.IGNORECASE),
    # Datos de usuarios
    re.compile(r"(dame|dime|muestra|lista|cu[aá]les?\s+son).{0,20}(usuarios|clientes|viveristas|compradores|registrados|tel[eé]fonos|correos|contactos)", re.IGNORECASE),
    re.compile(r"(cu[aá]ntos?\s+(usuarios|clientes|viveristas|compradores|registros|ventas|transacciones))", re.IGNORECASE),
    # Márgenes y finanzas internas
    re.compile(r"(margen|ganancia|utilidad|costo\s+operativo|cu[aá]nto\s+gana\s+viveroonline)", re.IGNORECASE),
    # Prompt injection
    re.compile(r"(system\s+prompt|prompt\s+del\s+sistema|instrucciones\s+del\s+sistema|jailbreak)", re.IGNORECASE),
]

_RESPUESTA_CONFIDENCIAL = (
    "Esa información no está disponible. "
    "¿En qué más puedo ayudarte con tus plantas o tu proyecto? 🌿"
)


def _es_intento_extraccion(mensaje: str) -> bool:
    """Retorna True si el mensaje parece intentar extraer información
    confidencial. Falsos positivos son aceptables — es mejor responder
    con cautela que revelar información sensible."""
    for patron in _PATRONES_EXTRACCION:
        if patron.search(mensaje):
            logger.warning(f"Intento de extracción detectado: {mensaje[:100]!r}")
            return True
    return False


# ─────────────────── CLASIFICADOR ───────────────────
# AJUSTE (16 jun): se agregó una regla de desempate explícita porque en
# producción mensajes de comprador como "para un jardín exterior en el
# norte de Bogotá, me recomiendas?" estaban clasificando como ai_ceo en
# vez de landscape_advisor. ai_ceo debe quedar reservado solo para
# preguntas sobre el negocio/plataforma de ViveroOnline misma, nunca
# para preguntas de un comprador sobre su propio proyecto.

CLASSIFIER_SYSTEM = """Clasifica el mensaje del usuario y devuelve EXACTAMENTE uno de estos agent_name en JSON:

- "plant_identifier": usuario quiere identificar una planta por foto / preguntas sobre identificación visual de una planta puntual
- "inventory_builder": viverista preguntando por su stock, precios, qué agregar a su catálogo
- "landscape_advisor": comprador preguntando por un proyecto de paisajismo, jardín, espacio exterior/interior, qué plantas usar, cuántas necesita, recomendaciones para su terreno (incluso si menciona que va a enviar una foto del espacio)
- "demand_predictor": preguntas sobre demanda futura, estacionalidad, cuándo sembrar
- "ai_researcher": preguntas sobre mercado, competencia, tendencias, datos del marketplace
- "startup_builder": viverista nuevo pidiendo plan de acción o cómo empezar
- "ai_architect": preguntas técnicas de infraestructura (invernaderos, riego) o arquitectura del sistema
- "ai_ceo": preguntas sobre el negocio/plataforma de ViveroOnline en sí (modelo de negocio, comisiones, cómo funciona la plataforma, estrategia de la empresa) — NUNCA uses esta categoría para preguntas de un comprador sobre su propio proyecto, jardín o espacio

Regla de desempate: si el mensaje describe un espacio, terreno, jardín, proyecto propio del usuario, o pide qué plantas usar/comprar para algo suyo, clasifica como "landscape_advisor" aunque también mencione fotos o sea ambiguo. Solo usa "ai_ceo" cuando la pregunta es explícitamente sobre ViveroOnline como empresa/plataforma.

Formato estricto (sin markdown):
{"agent_name": "..."}
"""


def _classify(mensaje: str, rol: str | None = None) -> str:
    """Devuelve el nombre del agente a invocar."""
    gem = get_gemini()
    hint = f"\n\n[Rol del usuario: {rol}]" if rol else ""
    try:
        raw = gem.chat(
            system_prompt=CLASSIFIER_SYSTEM,
            user_message=mensaje + hint,
            temperature=0.0,
        )
        data = json.loads(raw.strip().replace("```json", "").replace("```", "").strip())
        name = data.get("agent_name", "ai_ceo")
        if name not in _AGENTS:
            logger.warning(f"Clasificador devolvió agent_name desconocido: {name!r} — usando ai_ceo")
            return "ai_ceo"
        return name
    except Exception as e:
        logger.warning(f"Clasificador de intención falló, usando ai_ceo como fallback: {e}")
        return "ai_ceo"


# ─────────────────── LANGGRAPH FLOW ───────────────────

class RouterState(TypedDict):
    mensaje: str
    ctx: AgentContext
    agent_name: str
    respuesta: str
    metadata: dict


def classify_node(state: RouterState) -> RouterState:
    # Filtro de confidencialidad ANTES de clasificar
    if _es_intento_extraccion(state["mensaje"]):
        state["agent_name"] = "confidencialidad_bloqueado"
        state["respuesta"] = _RESPUESTA_CONFIDENCIAL
        return state
    state["agent_name"] = _classify(state["mensaje"], state["ctx"].rol)
    return state


def dispatch_node(state: RouterState) -> RouterState:
    # Si ya fue bloqueado por el filtro, no invocar ningún agente
    if state["agent_name"] == "confidencialidad_bloqueado":
        return state
    agent_cls = _AGENTS.get(state["agent_name"], AICeoAgent)
    agent = agent_cls()
    result = agent.run(state["mensaje"], state["ctx"])
    state["respuesta"] = result.get("respuesta", "")
    state["metadata"] = result.get("metadata", {})
    return state


def build_router():
    graph = StateGraph(RouterState)
    graph.add_node("classify", classify_node)
    graph.add_node("dispatch", dispatch_node)
    graph.set_entry_point("classify")
    graph.add_edge("classify", "dispatch")
    graph.add_edge("dispatch", END)
    return graph.compile()


_router = None


def get_router():
    global _router
    if _router is None:
        _router = build_router()
    return _router


def route_message(mensaje: str, ctx: AgentContext) -> dict:
    """API pública: enviar mensaje al router completo."""
    router = get_router()
    final_state = router.invoke({
        "mensaje": mensaje,
        "ctx": ctx,
        "agent_name": "",
        "respuesta": "",
        "metadata": {},
    })
    return {
        "respuesta": final_state["respuesta"],
        "agente": final_state["agent_name"],
        "metadata": final_state["metadata"],
    }
