"""🏗 AI_Architect — Asesor en decisiones técnicas y arquitectura.

Más útil para el equipo interno. Responde a viveristas sobre
infraestructura (invernaderos, riego), o al admin sobre arquitectura.
"""
from .base import Agent, AgentContext


ARCHITECT_SYSTEM = """Eres el AI_Architect de ViveroOnline. Asesoras en decisiones técnicas:

Para viveristas:
- Infraestructura física: invernaderos, tutorado, riego por goteo, acondicionamiento
- Optimización de espacio y rotación de cultivos
- Certificaciones (ICA, orgánico)

Para el equipo interno:
- Arquitectura del marketplace
- Integraciones técnicas

Responde con claridad técnica y recomendaciones accionables."""


class AIArchitectAgent(Agent):
    name = "ai_architect"
    description = "Asesoría técnica en infraestructura y arquitectura"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        history = [{"role": t.get("role", "user"), "content": t.get("content", "")}
                   for t in ctx.historial[-6:]]
        respuesta = self.gemini.chat(
            system_prompt=ARCHITECT_SYSTEM,
            user_message=mensaje,
            history=history,
            temperature=0.5,
        )
        return {"respuesta": respuesta, "metadata": {"agente": self.name}}
