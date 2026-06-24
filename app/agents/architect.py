"""🏗 AI_Architect — DESHABILITADO temporalmente.

Razón: el agente respondía con conocimiento general de Gemini sobre
infraestructura agrícola (invernaderos, riego, tutorado) sin tener
contenido validado para el clima específico de la Sabana de Bogotá
(2.600 msnm). Además, el system prompt original incluía la línea
"Para el equipo interno: arquitectura del marketplace, integraciones
técnicas" que abría un vector de fuga de información interna.

Estrategia post-lobotomización: el valor de las preguntas técnicas
se captura mediante contenido SEO en el blog WordPress
(viveroonline.com.co), donde:
- Cada entrada construye autoridad de marca y posicionamiento orgánico
- El bot redirige al blog (respuesta corta, alta intención de click)
- El cliente educado vuelve al marketplace más informado

Fecha de deshabilitación: 2026-06-23
Criterio de reactivación: si en algún momento se quiere reactivar
inteligencia técnica conversacional, hacerlo en formato híbrido
(FAQ específico de Sabana + Gemini con fuentes), nunca con
conocimiento general puro.

Para actualizar el link al blog (cuando exista entrada específica
de "infraestructura clima frío Sabana"): cambiar la constante
_URL_BLOG abajo.
"""
from .base import Agent, AgentContext


_URL_BLOG = "https://www.viveroonline.com.co"

_MENSAJE_HONESTO = (
    "🌱 ¡Buena pregunta! Para infraestructura técnica "
    "(invernaderos, riego, sustratos, clima frío) preparamos "
    "guías en nuestro blog:\n\n"
    f"📚 {_URL_BLOG}\n\n"
    "Si querés, también te puedo ayudar a buscar plantas o "
    "explorar el catálogo 🌿"
)


class AIArchitectAgent(Agent):
    """Stub deshabilitado. Mantiene interfaz pero no llama a Gemini.

    Ver docstring del módulo para razón y estrategia de reemplazo.
    """

    name = "ai_architect"
    description = "Asesoría técnica en infraestructura (deshabilitado, redirige a blog)"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        return {
            "respuesta": _MENSAJE_HONESTO,
            "metadata": {
                "agente": self.name,
                "estado": "deshabilitado",
                "razon": "contenido técnico migrado a blog SEO + cierre de vector de fuga interna",
                "redirige_a": _URL_BLOG,
                "costo_gemini_usd": 0,
            },
        }
