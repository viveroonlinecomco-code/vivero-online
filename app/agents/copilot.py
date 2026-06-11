"""Copilot Layer — Action Extractor para ViveroOnline.

Se ejecuta DESPUÉS de los 8 agentes existentes (no los reemplaza).
Lee la respuesta del agente + el mensaje del usuario y decide si hay
una acción concreta que proponer al viverista.

Si detecta una acción → propone al usuario con CTA claro esperando SÍ.
Si no detecta acción → pule la respuesta y agrega deep link relevante.

REGLA CRÍTICA: confirmed SIEMPRE es false en la primera propuesta.
Solo se ejecuta cuando el usuario responde SÍ a una propuesta anterior.

Acciones Sprint 1:
- actualizar_precio
- actualizar_stock
- actualizar_estado
- agregar_producto
- aprobar_pedido
- rechazar_pedido
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.agents.base import AgentContext
from app.services.gemini import get_gemini
from app.services.supabase import admin
from app.config import get_settings

logger = logging.getLogger(__name__)


# ─── URLs de la app ────────────────────────────────────────────────────────────

def _base_url() -> str:
    try:
        return get_settings().app_base_url
    except Exception:
        return "https://app.viveroonline.com.co"


# ─── Prompt maestro del Copilot ────────────────────────────────────────────────

COPILOT_SYSTEM = """Eres el "Action Wrapper" de ViveroOnline — marketplace B2B de plantas ornamentales en la Sabana de Bogotá, Colombia.

Tu trabajo NO es generar conocimiento nuevo. Tu trabajo es:
1. Leer el mensaje del viverista y la respuesta de nuestro agente IA.
2. Detectar si hay una acción concreta ejecutable.
3. Reformatear la respuesta para WhatsApp: corta, clara, con CTA que pide confirmación.

ACCIONES PERMITIDAS (Sprint 1):
- actualizar_precio: viverista menciona cambiar precio de una planta
- actualizar_stock: viverista menciona cambiar cantidad/stock
- actualizar_estado: viverista quiere marcar como agotado/disponible/reservado/en_crecimiento
- agregar_producto: viverista confirmó agregar una planta identificada por foto
- aprobar_pedido: viverista quiere aprobar un pedido pendiente
- rechazar_pedido: viverista quiere rechazar un pedido pendiente

REGLAS CRÍTICAS — LEER CON ATENCIÓN:

1. "confirmed" es SIEMPRE false en tu respuesta. SIN EXCEPCIÓN.
   - Un comando directo como "sube el precio a $15.000" es una PROPUESTA NUEVA, no una confirmación.
   - Solo el backend marca confirmed=true cuando el usuario responde "Sí" a una propuesta anterior.
   - NUNCA pongas "confirmed": true. Siempre false.

2. Si detectás una acción → proponela con un mensaje corto y terminá con:
   "¿Lo actualizo? Respondé *SÍ* para confirmar."

3. Si te FALTAN DATOS para ejecutar (no sabés qué planta es) → pon "needs_clarification": true
   y preguntá SOLO lo que falta. Ejemplo: "¿De cuál planta querés subir el precio?"

4. Si es saludo o consulta general sin acción → deja "acciones": [] y respondé en máximo 4 líneas.

5. Tono colombiano neutro. Sin markdown excesivo. Máximo 5 líneas de texto visible.

6. El markup de ViveroOnline es 18% — el comprador paga precio_base × 1.18.
   Siempre mostrá los dos precios cuando haya un precio involucrado.

7. Terminá SIEMPRE con una pregunta accionable o un link de la app.

EJEMPLOS CORRECTOS:

Usuario: "Sube el helecho a $15.000"
Respuesta correcta:
{
  "respuesta": "Helecho Boston 🌿\n• Tu precio: $15.000 COP\n• Comprador paga: $17.700 COP\n¿Lo actualizo? Respondé *SÍ* para confirmar.",
  "acciones": [{"type": "actualizar_precio", "confirmed": false, "needs_clarification": false, "params": {"inventario_id": null, "nombre": "Helecho", "nuevo_precio": 15000}}]
}

Usuario: "Tengo 20 helechos"
Respuesta correcta:
{
  "respuesta": "Helecho Boston — stock actual en tu catálogo.\n¿Querés actualizarlo a 20 unidades? Respondé *SÍ* para confirmar.",
  "acciones": [{"type": "actualizar_stock", "confirmed": false, "needs_clarification": false, "params": {"inventario_id": null, "nombre": "Helecho", "nuevo_stock": 20}}]
}

Usuario: "Hola cómo estás"
Respuesta correcta:
{
  "respuesta": "Hola 🌿 Todo bien por acá. ¿En qué te ayudo hoy?\nPodés subir precios, actualizar stock o consultar sobre tus plantas.",
  "acciones": []
}

FORMATO DE SALIDA — JSON estricto, sin markdown adicional, sin bloques de código:
{
  "respuesta": "Texto para WhatsApp. Corto. Termina en pregunta o acción.",
  "acciones": [
    {
      "type": "actualizar_precio",
      "confirmed": false,
      "needs_clarification": false,
      "params": {
        "inventario_id": null,
        "nombre": "Helecho Boston",
        "nuevo_precio": 15000
      }
    }
  ]
}

Si no hay acciones detectadas: "acciones": []
RECORDA: "confirmed" es SIEMPRE false. Nunca true.
"""


# ─── Clase principal ───────────────────────────────────────────────────────────

class CopilotLayer:
    """Wrapper que se ejecuta después de cualquier agente."""

    def __init__(self):
        self.gemini = get_gemini()

    def procesar(
        self,
        mensaje_usuario: str,
        respuesta_agente: str,
        ctx: AgentContext,
        inventario_snapshot: list[dict] | None = None,
    ) -> dict:
        """
        Procesa la respuesta del agente y extrae acciones si las hay.

        Args:
            mensaje_usuario: Lo que escribió el viverista
            respuesta_agente: Lo que respondió el agente LangGraph
            ctx: Contexto del usuario (vivero_id, rol, etc.)
            inventario_snapshot: Lista de items del inventario para resolver nombres

        Returns:
            {
                "respuesta": str,
                "acciones": list[dict],
                "raw_agente": str
            }
        """
        # Solo aplica para viveristas
        if ctx.rol not in ("viverista", "admin"):
            return {
                "respuesta": respuesta_agente,
                "acciones": [],
                "raw_agente": respuesta_agente,
            }

        # Construir contexto de inventario si está disponible
        inventario_ctx = ""
        if inventario_snapshot:
            lines = []
            for item in inventario_snapshot[:20]:
                lines.append(
                    f"  - ID:{item['inventario_id']} | {item['nombre']} | "
                    f"Stock:{item['stock']} | Precio:${item['precio']:,} COP | "
                    f"Estado:{item['estado']}"
                )
            inventario_ctx = "\n\nINVENTARIO ACTUAL DEL VIVERISTA:\n" + "\n".join(lines)

        # Construir el payload para el copilot
        user_payload = (
            f"MENSAJE DEL VIVERISTA: \"{mensaje_usuario}\"\n\n"
            f"RESPUESTA DE NUESTRO AGENTE: \"{respuesta_agente}\""
            f"{inventario_ctx}\n\n"
            f"BASE URL APP: {_base_url()}\n\n"
            f"RECORDATORIO: confirmed es SIEMPRE false en tu respuesta."
        )

        try:
            raw = self.gemini.chat(
                system_prompt=COPILOT_SYSTEM,
                user_message=user_payload,
                temperature=0.0,
            )
            # Limpiar markdown si viene
            clean = raw.strip()
            if "```" in clean:
                lines = clean.split("\n")
                clean = "\n".join(
                    l for l in lines
                    if not l.strip().startswith("```")
                ).strip()

            result = json.loads(clean)

            # Forzar confirmed=false en todas las acciones — seguridad adicional
            for accion in result.get("acciones", []):
                accion["confirmed"] = False

            # Resolver inventario_id si no vino y tenemos snapshot
            if inventario_snapshot and result.get("acciones"):
                result["acciones"] = self._resolver_inventario_ids(
                    result["acciones"], inventario_snapshot
                )

            return {
                "respuesta": result.get("respuesta", respuesta_agente),
                "acciones": result.get("acciones", []),
                "raw_agente": respuesta_agente,
            }

        except Exception as e:
            logger.warning(f"Copilot Layer falló, usando respuesta original: {e}")
            # Fallback seguro — devolver respuesta original del agente
            return {
                "respuesta": respuesta_agente,
                "acciones": [],
                "raw_agente": respuesta_agente,
            }

    def _resolver_inventario_ids(
        self, acciones: list[dict], inventario: list[dict]
    ) -> list[dict]:
        """
        Si una acción tiene nombre de planta pero no inventario_id,
        intenta resolverlo buscando en el snapshot del inventario.
        """
        for accion in acciones:
            params = accion.get("params", {})
            if params.get("inventario_id"):
                continue  # Ya tiene ID, no hace falta resolver

            nombre_buscado = (params.get("nombre") or "").lower().strip()
            if not nombre_buscado:
                continue

            # Búsqueda fuzzy simple por nombre
            for item in inventario:
                nombre_item = item.get("nombre", "").lower()
                if nombre_buscado in nombre_item or nombre_item in nombre_buscado:
                    params["inventario_id"] = item["inventario_id"]
                    params["nombre"] = item["nombre"]  # Normalizar nombre
                    break

        return acciones


# ─── Helper para obtener inventario del viverista ──────────────────────────────

def get_inventario_snapshot(vivero_id: int) -> list[dict]:
    """
    Obtiene el inventario del viverista en formato simplificado
    para que el copilot pueda resolver nombres → IDs.
    """
    try:
        db = admin()
        resp = db.table("inventario").select(
            "inventario_id, stock, precio_mayorista, estado_planta, "
            "plantas(nombre_comun)"
        ).eq("vivero_id", vivero_id).limit(150).execute()

        items = []
        for r in resp.data or []:
            planta = r.get("plantas") or {}
            items.append({
                "inventario_id": r["inventario_id"],
                "nombre": planta.get("nombre_comun", "Sin nombre"),
                "stock": r.get("stock", 0),
                "precio": float(r.get("precio_mayorista") or 0),
                "estado": r.get("estado_planta", "disponible"),
            })
        return items
    except Exception as e:
        logger.warning(f"No se pudo obtener inventario snapshot: {e}")
        return []


# ─── Instancia global ──────────────────────────────────────────────────────────

_copilot: CopilotLayer | None = None


def get_copilot() -> CopilotLayer:
    global _copilot
    if _copilot is None:
        _copilot = CopilotLayer()
    return _copilot
