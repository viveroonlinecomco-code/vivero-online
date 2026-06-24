"""FAQ local matcher para ai_ceo.

Carga las FAQs desde app/data/faq_config.json al importar y matchea
mensajes del usuario contra las keywords definidas. Si encuentra
match confiable retorna la respuesta predefinida (sin Gemini).
Si no hay match, retorna None para que el agente caiga a Gemini.

Beneficio: ~60-80% de mensajes a ai_ceo se resuelven a costo $0
y latencia <50ms.

Diseño:
- JSON externo permite editar respuestas sin tocar código Python
- Matching estricto: requiere ≥2 keywords O ≥1 keyword largo (≥7 chars)
- Normalización UTF-8 (lowercase + sin tildes) para robustez en español
"""
from __future__ import annotations

import json
import logging
import unicodedata
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Carga única del JSON al importar ───
_FAQ_PATH = Path(__file__).parent.parent / "data" / "faq_config.json"

try:
    with _FAQ_PATH.open("r", encoding="utf-8") as f:
        _FAQ_DATA = json.load(f)
    logger.info(
        f"FAQ local cargado: {len(_FAQ_DATA.get('faqs', []))} preguntas "
        f"(versión {_FAQ_DATA.get('version', '?')})"
    )
except FileNotFoundError:
    logger.error(f"FAQ config no encontrado en {_FAQ_PATH}")
    _FAQ_DATA = {"faqs": [], "fallback": None, "config": {}}
except json.JSONDecodeError as e:
    logger.error(f"FAQ config con JSON inválido: {e}")
    _FAQ_DATA = {"faqs": [], "fallback": None, "config": {}}


def _normalizar(texto: str) -> str:
    """Lowercase + quitar tildes + colapsar espacios."""
    if not texto:
        return ""
    # Quitar tildes (á → a, é → e, ñ → n, ü → u, etc.)
    nfkd = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Lowercase + colapso de espacios múltiples
    return " ".join(sin_tildes.lower().split())


def _contar_matches(mensaje_norm: str, keywords: list[str], kw_largo_min: int) -> tuple[int, int]:
    """Devuelve (total_matches, matches_largos)."""
    total = 0
    largos = 0
    for kw in keywords:
        kw_norm = _normalizar(kw)
        if kw_norm and kw_norm in mensaje_norm:
            total += 1
            if len(kw_norm) >= kw_largo_min:
                largos += 1
    return total, largos


def buscar_respuesta(mensaje: str) -> Optional[dict]:
    """Busca FAQ que matchee el mensaje.

    Args:
        mensaje: texto del usuario tal como llegó (sin normalizar).

    Returns:
        dict con keys 'respuesta', 'faq_id', 'confianza' si hay match.
        None si no hay match confiable (debe caer a Gemini).
    """
    if not mensaje or not _FAQ_DATA.get("faqs"):
        return None

    mensaje_norm = _normalizar(mensaje)
    if len(mensaje_norm) < 3:
        return None

    config = _FAQ_DATA.get("config", {})
    umbral_min = config.get("umbral_keywords_minimo", 2)
    kw_largo_min = config.get("keyword_largo_minimo_chars", 7)

    mejor_match = None
    mejor_score = 0

    for faq in _FAQ_DATA["faqs"]:
        keywords = faq.get("keywords", [])
        if not keywords:
            continue

        total, largos = _contar_matches(mensaje_norm, keywords, kw_largo_min)

        # Reglas de confianza (umbral estricto):
        # - ≥ umbral_min keywords matchean, O
        # - ≥1 keyword largo matchea (frases distintivas como "voy a perder")
        es_confiable = total >= umbral_min or largos >= 1

        if es_confiable and total > mejor_score:
            mejor_score = total
            mejor_match = faq

    if mejor_match:
        return {
            "respuesta": mejor_match["respuesta"],
            "faq_id": mejor_match["id"],
            "confianza": mejor_score,
        }

    return None


def get_fallback() -> Optional[dict]:
    """Devuelve la respuesta fallback configurada (usada si Gemini falla)."""
    fb = _FAQ_DATA.get("fallback")
    if fb and fb.get("respuesta"):
        return {
            "respuesta": fb["respuesta"],
            "faq_id": "fallback",
            "confianza": 0,
        }
    return None
