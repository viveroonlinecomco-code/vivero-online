"""📦 Inventory_Builder — Asesor de inventario y precios.
Sugiere precios, detecta stock bajo, propone reposición basada en
la demanda histórica y el inventario actual del viverista.
"""
from .base import Agent, AgentContext

# AJUSTE (18 jun): markup actualizado de 18% a 20% en texto, ejemplo y
# constante interna, alineado con el cambio en marketplace.py y pedidos.py.
INVENTORY_SYSTEM = """Eres el Inventory_Builder de ViveroOnline, especialista en gestión de inventario para viveristas de la Sabana de Bogotá.
Tu expertise:
- Precios mayoristas competitivos en COP
- Detección de stock bajo y reposición
- Agrupación por categorías (suculentas, ornamentales, aromáticas, árboles)
- Alertas de rotación lenta

⚠️ IMPORTANTE — MODELO DE PRECIOS DE VIVERO ONLINE:
El precio que el viverista ingresa al inventario es su PRECIO BASE (lo que él recibirá directamente).
ViveroOnline suma automáticamente un 20% de orquestación al precio base, y ESE es el precio que ve y paga el comprador.
Ese 20% cubre: comisión plataforma 8% + coordinación logística 6% + garantía de entrega 4% + margen operativo 2%.

Ejemplo:
- Viverista ingresa: $10.000 → Él recibirá $10.000
- Comprador verá: $12.000 (precio base + 20% de orquestación)

Cuando recomiendes precios, siempre recomienda el PRECIO BASE del viverista (lo que él quiere recibir).
Menciona siempre cuánto verá el comprador (precio_base × 1.20) para que el viverista tome decisiones informadas.

Formato recomendado al sugerir precios:
- Tu precio (lo que recibirás): $X.XXX COP
- Precio al comprador (con 20% orquestación): $X.XXX COP

Cuando tengas datos del inventario real del viverista, úsalos. Si no, recomienda con base en tu conocimiento de mercado de la Sabana de Bogotá."""


class InventoryBuilderAgent(Agent):
    name = "inventory_builder"
    description = "Asesor de stock, precios y reposición"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        # Traer inventario actual si hay vivero_id
        inventory_snapshot = ""
        if ctx.vivero_id:
            inventory_snapshot = self._get_inventory_summary(ctx.vivero_id)
        enriched = mensaje
        if inventory_snapshot:
            enriched = f"{mensaje}\n\n[INVENTARIO ACTUAL DEL VIVERISTA:\n{inventory_snapshot}]"
        history = [{"role": t.get("role", "user"), "content": t.get("content", "")}
                   for t in ctx.historial[-6:]]
        respuesta = self.gemini.chat(
            system_prompt=INVENTORY_SYSTEM,
            user_message=enriched,
            history=history,
            temperature=0.4,
        )
        return {
            "respuesta": respuesta,
            "metadata": {"agente": self.name, "datos_usados": bool(inventory_snapshot)},
        }

    def _get_inventory_summary(self, vivero_id: int) -> str:
        """Resumen del inventario actual con precio base y precio al comprador."""
        try:
            resp = self.db.table("inventario").select(
                "planta_id, altura_cm, stock, precio_mayorista, estado_planta, plantas(nombre_comun)"
            ).eq("vivero_id", vivero_id).limit(30).execute()
            if not resp.data:
                return "El viverista aún no tiene items en su inventario."
            MARKUP = 0.20
            lines = []
            for item in resp.data:
                nombre = item.get("plantas", {}).get("nombre_comun") if item.get("plantas") else "?"
                precio_base = int(float(item.get("precio_mayorista", 0) or 0))
                precio_comprador = round(precio_base * (1 + MARKUP))
                lines.append(
                    f"- {nombre}: {item.get('stock', 0)} uds, "
                    f"precio base ${precio_base:,} COP → comprador paga ${precio_comprador:,} COP, "
                    f"estado: {item.get('estado_planta', 'n/a')}"
                )
            return "\n".join(lines)
        except Exception:
            return ""
