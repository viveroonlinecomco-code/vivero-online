"""Copilot Layer — Action Extractor para ViveroOnline.

Flujo simplificado para viveristas con baja alfabetización digital:

1. Viverista escribe comando simple: "precio monstera 60000"
2. Bot busca plantas similares en el inventario (búsqueda fuzzy)
3. Si hay 1 resultado → propone directo con SÍ
4. Si hay varias → muestra lista numerada, viverista elige número
5. Viverista dice SÍ → executor actualiza Supabase

Comandos reconocidos:
- "precio [planta] [valor]" → actualizar_precio
- "stock [planta] [valor]" → actualizar_stock  
- "agotado [planta]" → actualizar_estado agotado
- "disponible [planta]" → actualizar_estado disponible
- "aprobar" / "rechazar" → aprobar/rechazar pedido
"""
from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from app.agents.base import AgentContext
from app.services.gemini import get_gemini
from app.services.supabase import admin
from app.config import get_settings

logger = logging.getLogger(__name__)


def _base_url() -> str:
    try:
        return get_settings().app_base_url
    except Exception:
        return "https://app.viveroonline.com.co"


# ─── Similitud entre nombres ───────────────────────────────────────────────────

def _similitud(a: str, b: str) -> float:
    """Calcula similitud entre dos strings. 1.0 = idénticos."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _buscar_plantas(query: str, inventario: list[dict], top: int = 3) -> list[dict]:
    """
    Busca plantas en el inventario por similitud con el query.
    Retorna lista ordenada por similitud descendente.
    """
    query_lower = query.lower().strip()
    resultados = []

    for item in inventario:
        nombre = item.get("nombre", "")
        nombre_lower = nombre.lower()

        # Match exacto o contiene
        if query_lower == nombre_lower:
            score = 1.0
        elif query_lower in nombre_lower or nombre_lower in query_lower:
            score = 0.85
        else:
            score = _similitud(query_lower, nombre_lower)

        if score >= 0.4:  # umbral mínimo
            resultados.append({**item, "_score": score})

    resultados.sort(key=lambda x: x["_score"], reverse=True)
    return resultados[:top]


# ─── Parser de comandos simples ────────────────────────────────────────────────

def _parsear_comando(mensaje: str) -> dict | None:
    """
    Intenta parsear comandos simples del viverista.
    
    Formatos reconocidos:
    - "precio monstera 60000" / "precio monstera a 60000"
    - "stock cinta 50" / "tengo 50 cintas"
    - "agotado helecho" / "helecho agotado"
    - "disponible monstera"
    - "rebaja monstera 10000" / "baja precio monstera 10000"
    
    Retorna dict con {tipo, nombre_planta, valor} o None si no reconoce.
    """
    msg = mensaje.lower().strip()
    
    # Precio: "precio X a Y" / "sube precio X a Y" / "baja precio X Y"
    m = re.search(r'(?:precio|sube|baja|rebaja|actualiza?|cambia?)\s+(?:precio\s+)?(?:de\s+|la\s+|el\s+)?(.+?)\s+(?:a\s+)?(\d[\d.,]*)', msg)
    if m:
        nombre = m.group(1).strip()
        valor_str = m.group(2).replace('.', '').replace(',', '')
        try:
            return {"tipo": "actualizar_precio", "nombre_planta": nombre, "valor": int(valor_str)}
        except ValueError:
            pass

    # Stock: "stock X Y" / "tengo Y X"
    m = re.search(r'(?:stock|cantidad|unidades?|tengo)\s+(?:de\s+|la\s+|el\s+)?(.+?)\s+(\d+)', msg)
    if m:
        nombre = m.group(1).strip()
        try:
            return {"tipo": "actualizar_stock", "nombre_planta": nombre, "valor": int(m.group(2))}
        except ValueError:
            pass
    
    m = re.search(r'tengo\s+(\d+)\s+(.+)', msg)
    if m:
        try:
            return {"tipo": "actualizar_stock", "nombre_planta": m.group(2).strip(), "valor": int(m.group(1))}
        except ValueError:
            pass

    # Agotado: "agotado X" / "X agotado" / "sin stock X"
    m = re.search(r'(?:agotado|sin stock|se acabó|acabó)\s+(?:la\s+|el\s+)?(.+)', msg)
    if m:
        return {"tipo": "actualizar_estado", "nombre_planta": m.group(1).strip(), "valor": "agotado"}
    m = re.search(r'(.+?)\s+(?:agotado|sin stock|se acabó)', msg)
    if m:
        nombre = m.group(1).replace('la ', '').replace('el ', '').strip()
        if len(nombre) > 2:
            return {"tipo": "actualizar_estado", "nombre_planta": nombre, "valor": "agotado"}

    # Disponible: "disponible X" / "X disponible"
    m = re.search(r'(?:disponible|reactiva?r?|activar?)\s+(?:la\s+|el\s+)?(.+)', msg)
    if m:
        return {"tipo": "actualizar_estado", "nombre_planta": m.group(1).strip(), "valor": "disponible"}

    return None


# ─── Clase principal ───────────────────────────────────────────────────────────

class CopilotLayer:

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
        Procesa el mensaje y retorna respuesta + acciones.
        
        Primero intenta parsear comandos simples localmente (sin Gemini).
        Si no reconoce el comando, usa Gemini como fallback.
        """
        if ctx.rol not in ("viverista", "admin"):
            return {"respuesta": respuesta_agente, "acciones": [], "raw_agente": respuesta_agente}

        inventario = inventario_snapshot or []

        # ── Intentar parsear comando simple primero (sin Gemini) ──────────────
        comando = _parsear_comando(mensaje_usuario)
        if comando and inventario:
            resultado = self._procesar_comando_local(comando, inventario)
            if resultado:
                return {**resultado, "raw_agente": respuesta_agente}

        # ── Fallback: usar Gemini ─────────────────────────────────────────────
        return self._procesar_con_gemini(mensaje_usuario, respuesta_agente, ctx, inventario)

    def _procesar_comando_local(self, comando: dict, inventario: list[dict]) -> dict | None:
        """
        Procesa un comando parseado localmente.
        Retorna dict con respuesta+acciones o None si no pudo resolverlo.
        """
        tipo = comando["tipo"]
        nombre_query = comando["nombre_planta"]
        valor = comando.get("valor")

        plantas = _buscar_plantas(nombre_query, inventario)

        if not plantas:
            return None  # No encontró nada → fallback a Gemini

        if len(plantas) == 1:
            # Una sola planta → proponer directo
            planta = plantas[0]
            return self._proponer_accion(tipo, planta, valor)

        # Varias plantas → mostrar opciones
        return self._proponer_seleccion(tipo, plantas, valor, nombre_query)

    def _proponer_accion(self, tipo: str, planta: dict, valor) -> dict:
        """Genera propuesta directa para una planta."""
        nombre = planta["nombre"]
        inv_id = planta["inventario_id"]
        precio_actual = planta.get("precio", 0)

        if tipo == "actualizar_precio":
            precio_comprador = round(valor * 1.18)
            respuesta = (
                f"*{nombre}* 🌿\n"
                f"• Tu precio: ${valor:,} COP\n"
                f"• Comprador paga: ${precio_comprador:,} COP\n"
                f"¿Lo actualizo? Respondé *SÍ* para confirmar."
            )
            accion = {
                "type": "actualizar_precio",
                "confirmed": False,
                "params": {"inventario_id": inv_id, "nombre": nombre, "nuevo_precio": valor}
            }

        elif tipo == "actualizar_stock":
            respuesta = (
                f"*{nombre}* 🌿\n"
                f"• Stock actual: {planta.get('stock', 0)} uds\n"
                f"• Nuevo stock: {valor} uds\n"
                f"¿Lo actualizo? Respondé *SÍ* para confirmar."
            )
            accion = {
                "type": "actualizar_stock",
                "confirmed": False,
                "params": {"inventario_id": inv_id, "nombre": nombre, "nuevo_stock": valor}
            }

        elif tipo == "actualizar_estado":
            estados_emoji = {"agotado": "🔴", "disponible": "🟢", "reservado": "🟡", "en_crecimiento": "🌱"}
            emoji = estados_emoji.get(valor, "")
            respuesta = (
                f"*{nombre}* → {emoji} *{valor}*\n"
                f"¿Lo marco así? Respondé *SÍ* para confirmar."
            )
            accion = {
                "type": "actualizar_estado",
                "confirmed": False,
                "params": {"inventario_id": inv_id, "nombre": nombre, "nuevo_estado": valor}
            }
        else:
            return None

        return {"respuesta": respuesta, "acciones": [accion]}

    def _proponer_seleccion(self, tipo: str, plantas: list[dict], valor, query: str) -> dict:
        """Genera mensaje con opciones numeradas."""
        tipo_label = {
            "actualizar_precio": f"subir precio a ${valor:,}",
            "actualizar_stock": f"actualizar stock a {valor}",
            "actualizar_estado": f"marcar como {valor}",
        }.get(tipo, tipo)

        lineas = [f"Encontré varias plantas similares a *{query}*:\n"]
        opciones = []
        for i, p in enumerate(plantas, 1):
            precio = p.get("precio", 0)
            stock = p.get("stock", 0)
            lineas.append(f"{i}️⃣ *{p['nombre']}* — ${precio:,} COP · {stock} uds")
            opciones.append({
                "num": i,
                "inventario_id": p["inventario_id"],
                "nombre": p["nombre"],
                "precio": precio,
                "stock": stock,
            })

        lineas.append(f"\n¿Cuál querés {tipo_label}? Respondé *1*, *2* o *3*")
        respuesta = "\n".join(lineas)

        seleccion = {
            "tipo": tipo,
            "valor": valor,
            "opciones": opciones,
        }

        return {"respuesta": respuesta, "acciones": [], "seleccion_pendiente": seleccion}

    def _procesar_con_gemini(
        self,
        mensaje_usuario: str,
        respuesta_agente: str,
        ctx: AgentContext,
        inventario: list[dict],
    ) -> dict:
        """Fallback: usa Gemini para casos complejos."""
        inventario_ctx = ""
        if inventario:
            lines = [f"  - ID:{item['inventario_id']} | {item['nombre']} | Stock:{item['stock']} | Precio:${item['precio']:,} COP"
                     for item in inventario[:50]]
            inventario_ctx = "\n\nINVENTARIO:\n" + "\n".join(lines)

        SYSTEM = """Eres el asistente de ViveroOnline para viveristas colombianos.
Respondé en máximo 4 líneas. Tono simple y directo.
Si el mensaje es una consulta general (no una acción), respondé con la info del agente resumida.
Si detectás una acción de inventario, extraela en JSON.

FORMATO DE SALIDA — solo JSON válido:
{"respuesta": "texto corto", "acciones": []}

Si no hay acción: {"respuesta": "...", "acciones": []}
"""
        user_msg = f'Mensaje: "{mensaje_usuario}"\nRespuesta agente: "{respuesta_agente[:200]}"{inventario_ctx}'

        try:
            raw = self.gemini.chat(system_prompt=SYSTEM, user_message=user_msg, temperature=0.0)
            clean = raw.strip()
            if "```" in clean:
                clean = "\n".join(l for l in clean.split("\n") if not l.strip().startswith("```")).strip()
            result = json.loads(clean)
            for accion in result.get("acciones", []):
                accion["confirmed"] = False
            return {
                "respuesta": result.get("respuesta", respuesta_agente),
                "acciones": result.get("acciones", []),
                "raw_agente": respuesta_agente,
            }
        except Exception as e:
            logger.warning(f"Copilot Gemini fallback error: {e}")
            return {"respuesta": respuesta_agente, "acciones": [], "raw_agente": respuesta_agente}

    def resolver_seleccion(self, numero: int, seleccion: dict) -> dict | None:
        """
        Resuelve una selección numerada del viverista.
        Retorna dict con respuesta+accion o None si número inválido.
        """
        opciones = seleccion.get("opciones", [])
        tipo = seleccion.get("tipo")
        valor = seleccion.get("valor")

        opcion = next((o for o in opciones if o["num"] == numero), None)
        if not opcion:
            nums = [str(o["num"]) for o in opciones]
            return {
                "respuesta": f"Opción inválida. Respondé {', '.join(nums[:-1])} o {nums[-1]}.",
                "acciones": []
            }

        planta = {
            "inventario_id": opcion["inventario_id"],
            "nombre": opcion["nombre"],
            "precio": opcion["precio"],
            "stock": opcion["stock"],
        }
        return self._proponer_accion(tipo, planta, valor)


# ─── Helper inventario ─────────────────────────────────────────────────────────

def get_inventario_snapshot(vivero_id: int) -> list[dict]:
    try:
        db = admin()
        resp = db.table("inventario").select(
            "inventario_id, stock, precio_mayorista, estado_planta, plantas(nombre_comun)"
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


# ─── Singleton ─────────────────────────────────────────────────────────────────

_copilot: CopilotLayer | None = None

def get_copilot() -> CopilotLayer:
    global _copilot
    if _copilot is None:
        _copilot = CopilotLayer()
    return _copilot
