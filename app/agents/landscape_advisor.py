"""🏡 Landscape_Advisor — Asesor de paisajismo B2B.

Ayuda a paisajistas, constructoras y conjuntos a elegir plantas
apropiadas para sus proyectos según clima, uso, tamaño del proyecto.
"""
from .base import Agent, AgentContext


LANDSCAPE_SYSTEM = """Eres el Landscape_Advisor de ViveroOnline, especialista en paisajismo B2B para la Sabana de Bogotá.

Experiencia en:
- Selección de especies para clima frío templado (2.400-2.800 msnm)
- Paisajismo residencial, corporativo y de conjuntos
- Proyectos de restauración ecológica
- Cálculo de cantidades para áreas
- Combinaciones estéticas (setos, macizos, cobertura, árboles)

Cuando el comprador describa un proyecto, sugiere:
1. Especies apropiadas con nombre científico
2. Cantidad estimada
3. Temporada de siembra ideal
4. Precios de referencia en COP por unidad

Sé práctico. Usa conocimiento de flora nativa andina cuando aplique (jazmín del Cabo, siete cueros, alcaparro, cucharo, etc.)."""


class LandscapeAdvisorAgent(Agent):
    name = "landscape_advisor"
    description = "Asesor de paisajismo para proyectos B2B"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        # Enriquecer con municipio si lo tenemos
        enriched = mensaje
        if ctx.municipio:
            enriched = f"[Proyecto en {ctx.municipio}, Sabana de Bogotá]\n{mensaje}"

        history = [{"role": t.get("role", "user"), "content": t.get("content", "")}
                   for t in ctx.historial[-6:]]
        respuesta = self.gemini.chat(
            system_prompt=LANDSCAPE_SYSTEM,
            user_message=enriched,
            history=history,
            temperature=0.6,
        )
        return {"respuesta": respuesta, "metadata": {"agente": self.name}}
