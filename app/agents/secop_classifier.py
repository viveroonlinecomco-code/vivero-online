"""Clasificador de procesos SECOP usando Gemini (google-genai SDK).

Para cada proceso con un `objeto` (descripción), extrae:
- es_relevante: si el proceso es REALMENTE sobre plantas
  (filtra falsos positivos como "plantas industriales")
- especies_mencionadas: nombres de plantas (común/científico)
- cantidad_estimada: número de unidades
- altura_estimada_cm: altura promedio
- tipo_proyecto: arborización / reforestación / jardinería / ornamental / otro
- fecha_estimada_entrega_iso: cronograma si aplica
- confianza: 0..1

Standalone (no hereda de Agent). Decisión consciente para Sprint 1
dado que es ingester admin-only.

Usa el SDK google-genai (no el legacy google-generativeai).
"""
from __future__ import annotations
import json
import logging
import os
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


MODEL_NAME = "gemini-2.5-flash"


class SecopAnalisis(BaseModel):
    """Resultado estructurado de la clasificación de un proceso."""
    es_relevante: bool = Field(
        description="True si el proceso es realmente sobre compra/suministro "
                    "de plantas vivas. False si matchea keyword por coincidencia "
                    "(ej: 'plantas industriales', 'plantas de tratamiento')."
    )
    especies_mencionadas: list[str] = Field(default_factory=list)
    cantidad_estimada: Optional[int] = Field(default=None)
    altura_estimada_cm: Optional[int] = Field(default=None)
    tipo_proyecto: Optional[str] = Field(
        default=None,
        description="'arborizacion_urbana' | 'reforestacion' | 'jardineria' | 'ornamental' | 'otro'"
    )
    fecha_estimada_entrega_iso: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD si el objeto da indicio del cronograma"
    )
    confianza: float = Field(ge=0.0, le=1.0)


PROMPT_TEMPLATE = """Analizá el siguiente proceso de contratación pública colombiana (SECOP II) y extraé información estructurada sobre plantas/árboles, si aplica.

Entidad: {entidad}
Ciudad: {ciudad}
Cuantía aproximada: {cuantia} COP

Objeto del proceso:
{objeto}

Devolvé JSON estricto con estos campos:

- es_relevante (bool): TRUE solo si el proceso es realmente sobre compra/suministro/instalación de plantas vivas, árboles, arbustos, palmas, césped o material vegetal vivo. FALSE si el match fue casual (ej: "plantas industriales", "plantas de tratamiento", "plantas eléctricas", "plantillas", "implantes").
- especies_mencionadas (list[str]): nombres de especies en el objeto (común y/o científico). Lista vacía si no se mencionan.
- cantidad_estimada (int|null): número total de unidades estimado. Null si no se infiere.
- altura_estimada_cm (int|null): altura promedio en cm si se menciona (convertir metros a cm). Null si no.
- tipo_proyecto (str|null): "arborizacion_urbana" | "reforestacion" | "jardineria" | "ornamental" | "otro"
- fecha_estimada_entrega_iso (str|null): YYYY-MM-DD si hay indicio del cronograma. Null si no.
- confianza (float 0..1): qué tan seguro estás del análisis general.

Respondé SOLO con el JSON, sin markdown, sin texto antes ni después."""


class SecopClassifier:
    """Clasifica procesos SECOP con Gemini usando el SDK google-genai.

    Uso:
        classifier = SecopClassifier()
        analisis, raw = classifier.classify(
            objeto="Suministro de árboles ornamentales ...",
            entidad="Jardín Botánico de Bogotá",
            ciudad="Bogotá D.C.",
            cuantia=50000000,
        )
    """

    def __init__(self):
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_AI_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "Falta GEMINI_API_KEY en el environment. "
                "Configurala en Vercel → Project Settings → Environment Variables."
            )
        self.client = genai.Client(api_key=api_key)

    def classify(
        self,
        objeto: str,
        entidad: Optional[str] = None,
        ciudad: Optional[str] = None,
        cuantia: Optional[float] = None,
    ) -> tuple[SecopAnalisis, dict]:
        """Clasifica un proceso. Devuelve (analisis_validado, raw_response_dict).

        Lanza Exception si Gemini falla o si el JSON es inválido.
        El caller debe envolver en try/except y continuar con el siguiente proceso.
        """
        prompt = PROMPT_TEMPLATE.format(
            entidad=entidad or "(no especificada)",
            ciudad=ciudad or "(no especificada)",
            cuantia=f"{cuantia:,.0f}" if cuantia else "(no especificada)",
            objeto=(objeto or "")[:3000],
        )

        try:
            response = self.client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
        except Exception as e:
            logger.error(f"Error llamando Gemini: {e}")
            raise

        raw_text = (response.text or "").strip()

        # Strip markdown fences defensivo (por si Gemini se equivoca)
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(lines[1:-1]) if len(lines) > 2 else raw_text

        try:
            raw_dict = json.loads(raw_text)
            analisis = SecopAnalisis(**raw_dict)
            return analisis, raw_dict
        except json.JSONDecodeError as e:
            logger.error(f"Gemini devolvió JSON inválido: {e}\nRaw: {raw_text[:500]}")
            raise
        except Exception as e:
            logger.error(f"Error validando schema: {e}\nRaw: {raw_text[:500]}")
            raise
