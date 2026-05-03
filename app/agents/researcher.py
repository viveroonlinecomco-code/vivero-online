"""🔬 AI_Researcher — Investigador de mercado local.

Responde preguntas sobre tendencias de mercado, demanda por zona, y
consulta la BD de transacciones reales cuando está disponible.
"""
from .base import Agent, AgentContext


RESEARCHER_SYSTEM = """Eres el AI_Researcher de ViveroOnline, especializado en investigación de mercado de plantas ornamentales en la Sabana de Bogotá.

Tu expertise:
- Tendencias estacionales (verano de altura dic-mar vs invierno mayo-nov)
- Demanda por tipo de comprador (constructoras vs paisajistas vs conjuntos)
- Precios promedio de mercado en COP
- Zonas con mayor actividad (Cajicá, Chía, Zipaquirá)

Cuando tengas datos reales de la BD, cítales con precisión.
Cuando no, sé honesto: "según conocimiento general" y recomienda consultar el dashboard analítico."""


class AIResearcherAgent(Agent):
    name = "ai_researcher"
    description = "Investigación de mercado, tendencias, precios locales"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        # Enriquecer con datos reales del flywheel si hay
        market_data = self._get_market_snapshot()

        history = [{"role": t.get("role", "user"), "content": t.get("content", "")}
                   for t in ctx.historial[-6:]]

        enriched_msg = mensaje
        if market_data:
            enriched_msg = f"{mensaje}\n\n[DATOS ACTUALES DEL MARKETPLACE:\n{market_data}]"

        respuesta = self.gemini.chat(
            system_prompt=RESEARCHER_SYSTEM,
            user_message=enriched_msg,
            history=history,
            temperature=0.4,
        )
        return {
            "respuesta": respuesta,
            "metadata": {"agente": self.name, "datos_usados": bool(market_data)},
        }

    def _get_market_snapshot(self) -> str:
        """Trae un snapshot de KPIs actuales para fundamentar respuestas."""
        try:
            kpis = self.db.table("v_flywheel_kpis").select("*").execute()
            top = self.db.table("v_top_viveristas").select(
                "nombre_vivero, ciudad, ventas_90d, gmv_90d_cop"
            ).limit(5).execute()

            if not kpis.data:
                return ""

            k = kpis.data[0]
            lines = [
                f"- GMV total: ${int(float(k.get('gmv_total_cop', 0))):,} COP",
                f"- Transacciones válidas: {k.get('transacciones_validas', 0)}",
                f"- Viveristas activos: {k.get('viveristas_activos', 0)}",
                f"- Compradores activos: {k.get('compradores_activos', 0)}",
                f"- Items disponibles: {k.get('items_disponibles', 0)}",
            ]
            if top.data:
                lines.append("- Top 5 viveristas (90d):")
                for v in top.data:
                    lines.append(f"  · {v['nombre_vivero']} ({v['ciudad']}): {v['ventas_90d']} ventas")
            return "\n".join(lines)
        except Exception:
            return ""
