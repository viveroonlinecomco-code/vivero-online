"""Cliente Gemini para:
- Vision: identificar planta desde imagen (modelo: gemini-2.5-flash)
- Embeddings: generar vectores 768-dim para pgvector (text-embedding-004)
- Chat: respuestas conversacionales para los agentes

FIX 11 sept 2026: Mejorar exception handling para errores de conexión.
Antes: RuntimeError en timeout/quota → FastAPI devuelve HTML 500 → JSON corrupto frontend
Ahora: Todos los errores devuelven PlantaIdentificada válida → JSON siempre correcto
"""
from __future__ import annotations
import base64
import json
import logging
from typing import Optional

from google import genai
from google.genai import types

from app.config import get_settings
from app.schemas.catalog import PlantaIdentificada

logger = logging.getLogger(__name__)

IDENTIFY_PROMPT = """Eres un experto botánico especializado en plantas ornamentales de la Sabana de Bogotá, Colombia (2.600 msnm, clima frío templado).

Analiza la imagen y devuelve un JSON con esta estructura EXACTA (sin markdown, sin texto extra):

{
  "nombre_comun": "string - nombre popular en español colombiano",
  "nombre_cientifico": "string - género y especie en latín",
  "familia_botanica": "string - familia botánica",
  "descripcion": "string - 2 oraciones máximo",
  "cuidados": "string - resumen en 1 oración",
  "luz": "directa|indirecta|sombra",
  "riego": "diario|semanal|quincenal|mensual",
  "advertencias": "string o null - si hay toxicidad, plagas comunes, o null",
  "confianza": 0.0 a 1.0,
  "precio_estimado_cop": número entero - precio mayorista sugerido en pesos colombianos para viveros de la Sabana de Bogotá,
  "altura_cm_estimada": número entero - altura en cm estimada desde la foto
}

IMPORTANTE — FILTRO DE CATEGORÍA:
ViveroOnline es un marketplace de plantas vivas ornamentales para paisajismo y decoración.
Si la imagen muestra cualquiera de los siguientes casos, retorna confianza = 0.0 y nombre_comun = "No aplica":
- Flores de corte (rosas, claveles, girasoles, lirios u otras flores cortadas sin raíz ni tierra)
- Plantas de cosecha agrícola (tomate, lechuga, maíz, fríjol, papa, cebolla u otros cultivos de alimento)
- Plantas artificiales, de tela, plástico o cualquier material sintético
- Bouquets, arreglos florales, coronas o flores procesadas
- Semillas o bulbos sin parte aérea visible
- Imágenes sin planta (paisajes, objetos, personas, etc.)

Solo identifica plantas vivas ornamentales: árboles, arbustos, palmas, helechos, suculentas, cactus, enredaderas, coberturas o plantas de interior/exterior con valor paisajístico.

Si no es una planta ornamental viva o la imagen es ilegible, retorna confianza = 0.0 y nombre_comun = "No identificada"."""


# Palabras clave en nombre_comun o nombre_cientifico que indican
# que Gemini identificó algo fuera de categoría pese al filtro del prompt.
# Segunda capa de defensa en Python por si el modelo ignora la instrucción.
_CATEGORIAS_RECHAZADAS = {
    # Flores de corte comunes
    "rosa cortada", "clavel", "girasol cortado", "lirio cortado",
    "crisantemo cortado", "gerbera cortada", "tulipán cortado",
    "flor de corte", "bouquet", "arreglo floral",
    # Cultivos agrícolas
    "tomate", "lechuga", "maíz", "maiz", "fríjol", "frijol",
    "papa", "cebolla", "zanahoria", "espinaca", "cilantro",
    "perejil", "albahaca", "menta", "hierbabuena",
    # Artificiales
    "planta artificial", "planta de plástico", "planta de tela",
    "flor artificial", "artificial",
}

_FAMILIAS_RECHAZADAS = {
    "solanaceae",   # tomate, papa (como cultivo)
    "asteraceae",   # girasol de corte (no todas — hay ornamentales)
}

# Géneros/especies agrícolas que nunca son ornamentales en este contexto
_GENEROS_AGRICOLAS = {
    "solanum lycopersicum",  # tomate
    "zea mays",              # maíz
    "phaseolus",             # fríjol
    "allium cepa",           # cebolla
    "lactuca sativa",        # lechuga
    "daucus carota",         # zanahoria
    "spinacia oleracea",     # espinaca
}


def _es_planta_rechazada(data: dict) -> tuple[bool, str]:
    """Retorna (True, motivo) si la planta identificada debe ser rechazada.
    Segunda capa de validación después del filtro en el prompt."""
    nombre = (data.get("nombre_comun") or "").lower()
    cientifico = (data.get("nombre_cientifico") or "").lower()
    familia = (data.get("familia_botanica") or "").lower()

    for keyword in _CATEGORIAS_RECHAZADAS:
        if keyword in nombre:
            return True, f"Categoría no aceptada: '{data.get('nombre_comun')}' es flor de corte, cultivo agrícola o planta artificial."

    for genero in _GENEROS_AGRICOLAS:
        if genero in cientifico:
            return True, f"Especie agrícola no aceptada: {data.get('nombre_cientifico')}."

    return False, ""


class GeminiService:
    def __init__(self) -> None:
        s = get_settings()
        self._client = genai.Client(api_key=s.gemini_api_key)
        self._model = s.gemini_model
        self._embedding_model = s.gemini_embedding_model

    # ─────────────────── VISION ───────────────────
    def identify_plant(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> PlantaIdentificada:
        """Identifica la planta usando Gemini Vision. Retorna PlantaIdentificada.

        FIX 11 sept 2026: Mejorar exception handling.
        Antes: RuntimeError en timeout/quota re-lanzaba → FastAPI 500 HTML
        Ahora: Todos los errores devuelven PlantaIdentificada válida con confianza=0.0
        """
        if not image_bytes or len(image_bytes) < 100:
            logger.warning("identify_plant: imagen vacía o muy pequeña")
            return PlantaIdentificada(
                nombre_comun="No identificada",
                confianza=0.0,
                advertencias="Imagen vacía o inválida",
            )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    IDENTIFY_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Gemini Vision error: {error_msg[:200]}")

            # ─── FIX: Capturar TODOS los errores de conexión ────
            # Timeout, rate limit, overload → devolver fallback válido
            if any(x in error_msg for x in [
                "429", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED",
                "timeout", "503", "REQUEST_TIMEOUT", "UNAVAILABLE",
                "Connection", "connection", "500", "SERVICE_UNAVAILABLE"
            ]):
                logger.warning(f"Gemini temporarily unavailable: {error_msg[:100]}")
                return PlantaIdentificada(
                    nombre_comun="No identificada",
                    confianza=0.0,
                    advertencias=(
                        "El servicio de identificación está temporalmente no disponible. "
                        "Por favor, intenta de nuevo en 1 minuto. "
                        "Si el problema persiste, contacta con soporte."
                    ),
                )

            # Error de formato de imagen
            if "400" in error_msg or "INVALID_ARGUMENT" in error_msg:
                logger.warning(f"Invalid image format: {error_msg[:100]}")
                return PlantaIdentificada(
                    nombre_comun="No identificada",
                    confianza=0.0,
                    advertencias="Formato de imagen no compatible. Intenta con otro archivo.",
                )

            # Error inesperado → fallback genérico
            logger.exception("Unexpected Gemini error")
            return PlantaIdentificada(
                nombre_comun="No identificada",
                confianza=0.0,
                advertencias=(
                    "Error procesando la imagen. "
                    "Por favor, intenta con otra foto o contacta con soporte."
                ),
            )

        try:
            data = json.loads(response.text)

            # ── Filtro de categoría (capa 2 en Python) ────────────────────────
            rechazada, motivo = _es_planta_rechazada(data)
            if rechazada:
                logger.info(f"identify_plant: planta rechazada por filtro de categoría — {motivo}")
                return PlantaIdentificada(
                    nombre_comun="No aplica",
                    confianza=0.0,
                    advertencias=(
                        "ViveroOnline solo acepta plantas vivas ornamentales. "
                        + motivo
                    ),
                )

            return PlantaIdentificada(**data)

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"identify_plant parse error: {e}")
            return PlantaIdentificada(
                nombre_comun="No identificada",
                confianza=0.0,
                advertencias=f"Error procesando respuesta. Intenta de nuevo.",
            )

    # ─────────────────── EMBEDDINGS ───────────────────
    def embed(self, text: str) -> list[float]:
        """Genera embedding de 768 dims para el texto dado."""
        response = self._client.models.embed_content(
            model=self._embedding_model,
            contents=text,
        )
        return response.embeddings[0].values

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embeddings en batch."""
        response = self._client.models.embed_content(
            model=self._embedding_model,
            contents=texts,
        )
        return [e.values for e in response.embeddings]

    # ─────────────────── CHAT ───────────────────
    def chat(
        self,
        system_prompt: str,
        user_message: str,
        history: Optional[list[dict]] = None,
        temperature: float = 0.7,
    ) -> str:
        """Chat conversacional con historial opcional."""
        contents = []
        if history:
            for turn in history:
                role = turn.get("role", "user")
                text = turn.get("content", "")
                contents.append(types.Content(
                    role=role if role in ("user", "model") else "user",
                    parts=[types.Part.from_text(text=text)],
                ))
        contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=user_message)],
        ))

        response = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
            ),
        )
        return response.text or ""


# Singleton
_instance: GeminiService | None = None


def get_gemini() -> GeminiService:
    global _instance
    if _instance is None:
        _instance = GeminiService()
    return _instance
