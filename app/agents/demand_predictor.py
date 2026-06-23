"""📈 Demand_Predictor — DESHABILITADO temporalmente.

Razón: el agente respondía con conocimiento general de Gemini sin datos
históricos reales del marketplace, lo cual podía inducir a viveristas a
tomar decisiones de siembra o compra erróneas con consecuencias
económicas reales.

Fecha de deshabilitación: 2026-06-23
Criterio de reactivación: cuando existan ≥6 meses de datos
transaccionales reales en el marketplace Y una tabla
`estacionalidad_plantas` poblada con datos históricos por especie.

La clase mantiene su interfaz para no romper el router; ahora devuelve
una respuesta hardcodeada honesta sin llamar a Gemini (costo $0).
Para reactivar: ver historial git de este archivo.
"""
from .base import Agent, AgentContext


_MENSAJE_HONESTO = (
    "📊 Nuestra herramienta de predicción de demanda está en construcción. "
    "Estamos esperando tener más datos reales del marketplace para darte "
    "proyecciones confiables — no queremos darte una recomendación que "
    "te haga sembrar o comprar mal.\n\n"
    "Mientras tanto te puedo ayudar con:\n"
    "🌿 Encontrar plantas según tu proyecto o clima\n"
    "📸 Identificar una planta desde una foto\n"
    "🏗️ Recomendaciones para clima frío de la Sabana de Bogotá\n\n"
    "¿Con cuál seguimos?"
)


class DemandPredictorAgent(Agent):
    """Stub deshabilitado. Mantiene interfaz pero no llama a Gemini.

    Ver docstring del módulo para razón y criterio de reactivación.
    """

    name = "demand_predictor"
    description = "Predicción de demanda estacional (deshabilitado)"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        return {
            "respuesta": _MENSAJE_HONESTO,
            "metadata": {
                "agente": self.name,
                "estado": "deshabilitado",
                "razon": "esperando datos reales del marketplace",
                "costo_gemini_usd": 0,
            },
        }
