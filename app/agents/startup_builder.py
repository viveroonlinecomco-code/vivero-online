"""🚀 Startup_Builder — Plan de acción concreto para viveristas.

Ayuda a viveristas nuevos a empezar: qué plantas cargar primero,
cómo fijar precios, cómo atraer primeros compradores.
"""
from .base import Agent, AgentContext


STARTUP_SYSTEM = """Eres el Startup_Builder de ViveroOnline. Tu rol es dar PLANES DE ACCIÓN concretos, numerados y medibles para viveristas que están empezando en el marketplace.

Para cada consulta, devuelve:
1. Diagnóstico breve (1 oración)
2. Plan de acción en pasos numerados (máximo 5 pasos)
3. Métricas para medir éxito en 30 días

Tono: motivador pero realista. En COP. Ejemplos concretos de la Sabana de Bogotá."""


class StartupBuilderAgent(Agent):
    name = "startup_builder"
    description = "Planes de acción concretos para viveristas"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        # Enriquecer si sabemos el municipio del viverista
        enriched = mensaje
        if ctx.municipio:
            enriched = f"[Viverista en {ctx.municipio}]\n{mensaje}"

        history = [{"role": t.get("role", "user"), "content": t.get("content", "")}
                   for t in ctx.historial[-6:]]
        respuesta = self.gemini.chat(
            system_prompt=STARTUP_SYSTEM,
            user_message=enriched,
            history=history,
            temperature=0.6,
        )
        return {"respuesta": respuesta, "metadata": {"agente": self.name}}
