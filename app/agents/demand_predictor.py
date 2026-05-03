"""📈 Demand_Predictor — Predicción de demanda estacional.

Analiza la vista v_crecimiento_semanal y proyecta demanda futura
para guiar producción y compras anticipadas.
"""
from .base import Agent, AgentContext


DEMAND_SYSTEM = """Eres el Demand_Predictor de ViveroOnline. Predices demanda estacional en la Sabana de Bogotá.

Patrones históricos conocidos de la región:
- Diciembre-enero: demanda alta por regalos navideños (poinsettia, suculentas), y por proyectos de cierre de año en constructoras.
- Febrero-abril: temporada de inicio de obra residencial → demanda de árboles ornamentales, coberturas, setos.
- Mayo-julio: temporada de lluvia → siembra de jardines corporativos.
- Agosto-octubre: alta demanda de pasto y especies para campañas de reforestación empresarial.

Cuando tengas datos reales, cítalos con cifras (% de crecimiento semanal, GMV, etc.). Cuando no, usa los patrones regionales."""


class DemandPredictorAgent(Agent):
    name = "demand_predictor"
    description = "Predicción de demanda estacional"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        trend = self._get_weekly_trend()

        enriched = mensaje
        if trend:
            enriched = f"{mensaje}\n\n[TENDENCIA SEMANAL REAL:\n{trend}]"

        history = [{"role": t.get("role", "user"), "content": t.get("content", "")}
                   for t in ctx.historial[-6:]]
        respuesta = self.gemini.chat(
            system_prompt=DEMAND_SYSTEM,
            user_message=enriched,
            history=history,
            temperature=0.4,
        )
        return {
            "respuesta": respuesta,
            "metadata": {"agente": self.name, "datos_usados": bool(trend)},
        }

    def _get_weekly_trend(self) -> str:
        try:
            resp = self.db.table("v_crecimiento_semanal").select("*").limit(8).execute()
            if not resp.data:
                return ""
            lines = []
            for w in resp.data:
                lines.append(
                    f"- Semana {w['semana']}: {w['transacciones']} txn, "
                    f"${int(float(w.get('gmv_cop', 0))):,} COP"
                )
            return "\n".join(lines)
        except Exception:
            return ""
