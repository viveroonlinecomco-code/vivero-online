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

AJUSTE (23 jun 2026): el bot respondía con la ciudad del VIVERO en lugar
de la ciudad del CLIENTE. Cuando el cliente decía "para un jardín en Cota",
el bot respondía "para tu jardín en Cajicá" (donde está vivero 11).

Causa raíz:
1. ctx.municipio estaba vacío para clientes no registrados, así que la
   ciudad mencionada por el cliente en su mensaje no se estructuraba
   en el contexto enviado a Gemini.
2. El system prompt incluía un ejemplo anclado a "en Cajicá", y Cajicá
   también aparecía como ciudad del vivero en las OPCIONES — Gemini
   replicaba ese patrón por defecto sin contexto explícito de proyecto.

Fix:
- Nuevo método _extraer_ciudad_del_mensaje() detecta municipios de la
  Sabana en el mensaje del usuario vía regex con word boundaries y
  normalización UTF-8 (sin tildes).
- Prioridad de ciudad: mensaje del cliente > ctx.municipio > sin ciudad.
- System prompt agrega REGLA INQUEBRANTABLE 2 sobre ciudad del proyecto
  vs ciudad del vivero, y muestra dos ejemplos (con/sin ciudad) en lugar
  de uno solo anclado a Cajicá.
"""
from __future__ import annotations
import re
import unicodedata

from .base import Agent, AgentContext

MARKETPLACE_BASE_URL = "https://app.viveroonline.com.co/marketplace/producto"

# Municipios de la Sabana de Bogotá con forma canónica (con tildes).
# El matching es contra texto normalizado sin tildes (lowercase + NFKD).
# Word boundaries (\b) evitan falsos positivos tipo "Madrid" matcheando
# dentro de palabras compuestas.
_MUNICIPIOS_SABANA = [
    ("Cota", r"\bcota\b"),
    ("Cajicá", r"\bcajica\b"),
    ("Chía", r"\bchia\b"),
    ("Zipaquirá", r"\bzipaquira\b"),
    ("Sopó", r"\bsopo\b"),
    ("La Calera", r"\bla calera\b"),
    ("Tabio", r"\btabio\b"),
    ("Tenjo", r"\btenjo\b"),
    ("Facatativá", r"\bfacatativa\b"),
    ("Madrid", r"\bmadrid\b"),
    ("Mosquera", r"\bmosquera\b"),
    ("Funza", r"\bfunza\b"),
    ("Tocancipá", r"\btocancipa\b"),
    ("Bogotá", r"\bbogota\b"),
]

LANDSCAPE_SYSTEM = """Eres el Landscape_Advisor de ViveroOnline, asesor de paisajismo B2B para la Sabana de Bogotá.

REGLA INQUEBRANTABLE 1 (catálogo): solo puedes recomendar plantas que aparezcan en la lista de "OPCIONES DISPONIBLES" que se te entrega abajo. NUNCA inventes especies, nombres científicos ni precios que no estén en esa lista, aunque tu conocimiento general de jardinería te sugiera otras. Si la lista está vacía o no calza con lo que pide el usuario, dilo claramente y sugiere la categoría más cercana que sí exista en la lista, o invita a contactar directamente al equipo.

REGLA INQUEBRANTABLE 2 (ciudad del proyecto): si el contexto del mensaje indica "[Proyecto en {ciudad}, Sabana de Bogotá]", usá ESA ciudad en tu respuesta. NUNCA reemplaces la ciudad del proyecto por la ciudad del vivero, aunque sean distintas — la entrega coordinada cubre toda la Sabana, así que no importa dónde esté físicamente el vivero. Si NO se indica ciudad del proyecto, NO menciones ninguna ciudad: habla de "tu proyecto" o "tu jardín" sin localización, NUNCA inventes ni asumas.

FORMATO DE RESPUESTA (obligatorio, sin excepciones):
- Máximo 3-4 líneas en total. Nada de párrafos largos ni explicaciones botánicas extensas.
- Recomienda 2-3 opciones como máximo, cada una con nombre + precio en COP.
- Cierra SIEMPRE con el link directo a la opción principal recomendada (te lo entregamos ya armado, solo cópialo).
- Tono práctico y vendedor, no de enciclopedia. El objetivo es que el comprador haga clic y compre, no que aprenda botánica.

Ejemplos de estilo de respuesta ideal:

CASO A (con ciudad del proyecto en el contexto):
"Para tu jardín en Cota te recomiendo Bugambilia ($33.925) para cobertura con color, o Aralia Millonaria ($25.444) si buscas follaje denso. Mira el detalle y compra aquí: [link]"

CASO B (sin ciudad del proyecto en el contexto):
"Para tu proyecto te recomiendo Bugambilia ($33.925) para cobertura con color, o Aralia Millonaria ($25.444) si buscas follaje denso. Mira el detalle y compra aquí: [link]"
"""


class LandscapeAdvisorAgent(Agent):
    name = "landscape_advisor"
    description = "Asesor de paisajismo para proyectos B2B"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        opciones = self._get_opciones_disponibles(mensaje)
        opciones_texto = self._formatear_opciones(opciones)

        # Prioridad de ciudad: la mencionada en el mensaje del cliente
        # > ctx.municipio (registro del usuario, puede estar vacío).
        # El cliente puede tener proyecto en una ciudad distinta a su
        # ciudad registrada, así que el mensaje gana siempre.
        ciudad_proyecto = self._extraer_ciudad_del_mensaje(mensaje) or ctx.municipio

        enriched = mensaje
        if ciudad_proyecto:
            enriched = f"[Proyecto en {ciudad_proyecto}, Sabana de Bogotá]\n{mensaje}"
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
            "metadata": {
                "agente": self.name,
                "opciones_encontradas": len(opciones),
                "ciudad_proyecto": ciudad_proyecto,
            },
        }

    def _extraer_ciudad_del_mensaje(self, mensaje: str) -> str | None:
        """Detecta si el cliente mencionó una ciudad de la Sabana.

        Matching con word boundaries sobre texto normalizado (lowercase
        + sin tildes) para evitar falsos positivos. Devuelve la primera
        ciudad encontrada en forma canónica (con tildes). Si no hay
        match, devuelve None.
        """
        if not mensaje:
            return None
        nfkd = unicodedata.normalize("NFKD", mensaje.lower())
        msg_norm = "".join(c for c in nfkd if not unicodedata.combining(c))

        for ciudad_canonica, patron in _MUNICIPIOS_SABANA:
            if re.search(patron, msg_norm):
                return ciudad_canonica
        return None

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
