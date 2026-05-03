"""🌿 Plant_Identifier — Identifica plantas desde imagen.

Combina YOLO-11 (detección + recorte) + Gemini Vision (identificación).
Si no hay YOLO API key, usa solo Gemini directamente.
"""
from typing import Optional

from .base import Agent, AgentContext
from app.schemas.catalog import PlantaIdentificada


class PlantIdentifierAgent(Agent):
    name = "plant_identifier"
    description = "Identifica plantas desde fotos usando YOLO + Gemini"

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        """Para uso conversacional: explica qué hace. La identificación real
        se hace directamente via servicio Gemini desde la ruta /identificar."""
        return {
            "respuesta": (
                "Puedo identificar plantas desde una foto. "
                "Ve a la pestaña 'Agregar con IA' y sube la imagen de la planta. "
                "Detecto especie, sugiero precio de mercado para la Sabana de Bogotá, "
                "y agrego la información a tu catálogo automáticamente."
            ),
            "metadata": {"agente": self.name, "action": "redirect_to_camera"},
        }

    def identify_from_bytes(self, image_bytes: bytes, mime: str = "image/jpeg") -> PlantaIdentificada:
        """Uso directo desde el endpoint de identificación."""
        # TODO: cuando esté listo YOLO-11, preprocesar con crop antes de Gemini
        return self.gemini.identify_plant(image_bytes, mime)
