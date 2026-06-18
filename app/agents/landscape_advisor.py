"""🏡 Landscape_Advisor — Asesor de paisajismo B2B.
Ayuda a paisajistas, constructoras y conjuntos a elegir plantas
apropiadas para sus proyectos según clima, uso, tamaño del proyecto.

AJUSTE (18 jun): el agente respondía con conocimiento general de Gemini
(especies y precios inventados que NO existen en el catálogo real, ej.
"Sietecueros", "Cucharo" — verificado por query, cero coincidencias).
Esto generaba recomendaciones que el comprador no podía comprar en la
plataforma, matando la conversión en vez de ayudarla.

Ahora el agente:
1. Consulta el inventario real disponible (plantas + inventario + viveros)
   filtrando por uso_paisajistico, stock > 0 y estado disponible.
2. Le pasa esa lista a Gemini como contexto OBLIGATORIO — el prompt le
   exige recomendar SOLO de esa lista, nunca inventar especies/precios.
3. Responde corto (3-4 líneas, 2-3 opciones con precio) y cierra siempre
   con un link directo al producto en el marketplace (inventario_id, que
   es el ID real de la oferta comprable — no planta_id, que es genérico
   y no identifica qué vivero/precio/stock corresponde).
"""
from .base import Agent, AgentContext

MARKETPLACE_BASE_URL = "https://app.viveroonline.com.co/marketplace/producto"

LANDSCAPE_SYSTEM = """Eres el Landscape_Advisor de ViveroOnline, asesor de paisajismo B2B para la Sabana de Bogotá.

REGLA INQUEBRANTABLE: solo puedes recomendar plantas que aparezcan en la lista de "OPCIONES DISPONIBLES" que se te entrega abajo. NUNCA inventes especies, nombres científicos ni precios que no estén en esa lista, aunque tu conocimiento general de jardinería te sugiera otras. Si la lista está vacía o no calza con lo que pide el usuario, dilo claramente y sugiere la categoría más cercana que sí exista en la lista, o invita a contactar directamente al equipo.

FORMATO DE RESPUESTA (obligatorio, sin excepciones):
- Máximo 3-4 líneas en total. Nada de párrafos largos ni explicaciones botánicas extensas.
- Recomienda 2-3 opciones como máximo, cada una con nombre + precio en COP.
- Cierra SIEMPRE con el link directo a la opción principal recomendada (te lo entregamos ya armado, solo cópialo).
- Tono práctico y vendedor, no de enciclopedia. El objetivo es que el comprador haga clic y compre, no que aprenda botánica.

Ejemplo de estilo de respuesta ideal:
"Para exterior en clima frío en Cajicá te recomiendo Bugambilia ($33.925) para cobertura con color, o Aralia Millonaria ($25.444) si buscas follaje denso. Mira el detalle y compra aquí: [link]"
"""


class LandscapeAdvisorAgent(Agent):
    name = "landscape_advisor"
    description = "Asesor de paisajismo para proyectos B2B"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        opciones = self._get_opciones_disponibles(mensaje)
        opciones_texto = self._formatear_opciones(opciones)

        enriched = mensaje
        if ctx.municipio:
            enriched = f"[Proyecto en {ctx.municipio}, Sabana de Bogotá]\n{mensaje}"
        enriched += f"\n\n[OPCIONES DISPONIBLES (catálogo real, con stock):\n{opciones_texto}]"

        history = [{"role": t.get("role", "user"), "content": t.get("content", "")}
                   for t in ctx.historial[-6:]]

        respuesta = self.gemini.chat(
            system_prompt=LANDSCAPE_SYSTEM,
            user_message=enriched,
            history=history,
            temperature=0.4,
        )
        return {
            "respuesta": respuesta,
            "metadata": {"agente": self.name, "opciones_encontradas": len(opciones)},
        }

    def _get_opciones_disponibles(self, mensaje: str, limit: int = 8) -> list[dict]:
        """Trae plantas con uso paisajístico y stock real disponible.

        No intenta hacer matching semántico sofisticado del mensaje contra
        el catálogo (eso requeriría el campo `embedding` de plantas, que
        existe pero no se usa aquí todavía) — por ahora trae el universo de
        opciones de paisajismo disponibles y deja que Gemini elija las más
        relevantes según lo que el usuario describió, siempre dentro de esa
        lista real.
        """
        try:
            # Se parte de `plantas` (donde vive uso_paisajistico) en vez de
            # `inventario`, para evitar filtrar una columna de tabla referenciada
            # vía join anidado — patrón frágil en clientes Postgrest. El join
            # hacia inventario/viveros sigue el mismo formato ya validado en
            # producción por InventoryBuilderAgent._get_inventory_summary.
            resp = (
                self.db.table("plantas")
                .select(
                    "planta_id, nombre_comun, nombre_cientifico, uso_paisajistico, clima_ideal, "
                    "inventario(inventario_id, stock, precio_mayorista, estado_planta, "
                    "viveros(nombre_vivero, ciudad))"
                )
                .not_.is_("uso_paisajistico", "null")
                .eq("activa", True)
                .limit(limit * 3)  # sobre-pedir: luego se filtra por stock/estado en Python
                .execute()
            )
            return self._aplanar_y_filtrar(resp.data or [], limit)
        except Exception:
            return []

    def _aplanar_y_filtrar(self, plantas_con_inventario: list[dict], limit: int) -> list[dict]:
        """Convierte el resultado anidado (planta → [inventario...]) en una
        lista plana de ítems comprables, filtrando por stock > 0 y estado
        disponible (el filtro no se puede aplicar de forma fiable dentro de
        la tabla referenciada en el .select() anidado)."""
        opciones = []
        for planta in plantas_con_inventario:
            items_inventario = planta.get("inventario") or []
            for item in items_inventario:
                if item.get("estado_planta") != "disponible":
                    continue
                if not item.get("stock") or item["stock"] <= 0:
                    continue
                opciones.append({
                    "inventario_id": item.get("inventario_id"),
                    "stock": item.get("stock"),
                    "precio_mayorista": item.get("precio_mayorista"),
                    "estado_planta": item.get("estado_planta"),
                    "plantas": {
                        "planta_id": planta.get("planta_id"),
                        "nombre_comun": planta.get("nombre_comun"),
                        "nombre_cientifico": planta.get("nombre_cientifico"),
                        "uso_paisajistico": planta.get("uso_paisajistico"),
                        "clima_ideal": planta.get("clima_ideal"),
                    },
                    "viveros": item.get("viveros") or {},
                })
                if len(opciones) >= limit:
                    return opciones
        return opciones

    def _formatear_opciones(self, opciones: list[dict]) -> str:
        if not opciones:
            return "(sin opciones disponibles en este momento — avisa al usuario y sugiere contactar al equipo)"

        lines = []
        for item in opciones:
            planta = item.get("plantas") or {}
            vivero = item.get("viveros") or {}
            nombre = planta.get("nombre_comun") or planta.get("nombre_cientifico") or "?"
            precio = int(float(item.get("precio_mayorista", 0) or 0))
            usos = ", ".join(planta.get("uso_paisajistico") or [])
            inventario_id = item.get("inventario_id")
            link = f"{MARKETPLACE_BASE_URL}/{inventario_id}"
            lines.append(
                f"- {nombre} | ${precio:,} COP | uso: {usos} | "
                f"vivero: {vivero.get('nombre_vivero', '?')} ({vivero.get('ciudad', '?')}) | "
                f"link: {link}"
            )
        return "\n".join(lines)
