"""Cliente Gemini para:
- Vision: identificar planta desde imagen (modelo: gemini-2.5-flash)
- Embeddings: generar vectores 768-dim para pgvector (text-embedding-004)
- Chat: respuestas conversacionales para los agentes
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

Si no es una planta o la imagen es ilegible, retorna confianza = 0.0 y nombre_comun = "No identificada"."""


class GeminiService:
    def __init__(self) -> None:
        s = get_settings()
        self._client = genai.Client(api_key=s.gemini_api_key)
        self._model = s.gemini_model
        self._embedding_model = s.gemini_embedding_model

    # ─────────────────── VISION ───────────────────
    def identify_plant(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> PlantaIdentificada:
        """Identifica la planta usando Gemini Vision. Retorna PlantaIdentificada."""
        # Validar que los bytes no estén vacíos
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

            # Cuota agotada
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                raise RuntimeError("cuota_agotada")

            # Imagen inválida para Gemini
            if "400" in error_msg or "INVALID_ARGUMENT" in error_msg:
                return PlantaIdentificada(
                    nombre_comun="No identificada",
                    confianza=0.0,
                    advertencias="Formato de imagen no compatible",
                )

            # Cualquier otro error
            raise RuntimeError(f"gemini_error:{error_msg[:100]}")

        try:
            data = json.loads(response.text)
            return PlantaIdentificada(**data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"identify_plant parse error: {e}")
            return PlantaIdentificada(
                nombre_comun="No identificada",
                confianza=0.0,
                advertencias=f"Error parseo IA: {e}",
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
