"""LangGraph router: clasifica intención → dispacha al agente correcto.

Arquitectura:
    (ENTRY) → classify_intent → dispatch → agent.run() → END

La clasificación usa Gemini Flash en modo estructurado para devolver
el `agent_name`. Si falla, cae en AI_CEO como fallback.
"""
from __future__ import annotations
import json
import logging
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
        # AJUSTE (16 jun): antes este fallo era completamente silencioso —
        # caía en ai_ceo sin dejar rastro en logs. Ahora queda registrado
        # para poder distinguir "el clasificador falló" de "el clasificador
        # decidió genuinamente ai_ceo".
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
    state["agent_name"] = _classify(state["mensaje"], state["ctx"].rol)
    return state


def dispatch_node(state: RouterState) -> RouterState:
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
