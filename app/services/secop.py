"""Cliente HTTP para la API pública de SECOP II en datos.gov.co (Socrata).

API pública (con app_token recomendado para rate limit) que soporta SoQL.
Docs: https://dev.socrata.com/foundry/www.datos.gov.co/p6dx-8zbt

Datasets relevantes:
- p6dx-8zbt: SECOP II - Procesos de Contratación  (target Sprint 1)
- jbjy-vk9h: SECOP II - Contratos Electrónicos
- b6m4-qgqv: SECOP II - PAA Encabezado (Plan Anual de Adquisiciones)

NOTAS HISTÓRICAS:
- La versión inicial filtraba sobre `objeto_del_contrato` (columna de
  SECOP I). El dataset target es SECOP II que usa
  `descripci_n_del_procedimiento`. Fix aplicado 2026-05-23 (PR #35).
- A partir de 2026, datos.gov.co requiere X-App-Token header para
  queries SoQL. Sin token devuelve 400 por rate limit. Fix PR #36.
- Socrata trunca nombres de columnas a 30 chars. La columna de fecha
  de publicación NO es `fecha_de_publicacion_del_proceso` sino
  `fecha_de_publicacion_del` (truncada). Fix aplicado 2026-05-23.
"""
from __future__ import annotations
import logging
import os
from typing import Optional, Any

import httpx


logger = logging.getLogger(__name__)


# ─── IDs de datasets de Socrata ───
DATASET_PROCESOS = "p6dx-8zbt"
DATASET_CONTRATOS = "jbjy-vk9h"
DATASET_PAA = "b6m4-qgqv"

BASE_URL = "https://www.datos.gov.co/resource"
DEFAULT_TIMEOUT = 30.0

# Columnas REALES del dataset SECOP II Procesos (verificadas contra
# response de Socrata el 2026-05-23). Socrata trunca a 30 chars.
SECOP_II_DESCRIPCION_COL = "descripci_n_del_procedimiento"
SECOP_II_CIUDAD_COL = "ciudad_entidad"
SECOP_II_FECHA_PUB_COL = "fecha_de_publicacion_del"  # truncado, NO "_proceso"


# ─── Keywords para filtrar procesos relevantes a plantas ───
KEYWORDS_PLANTAS = [
    "arborizacion", "arborización",
    "siembra", "plantacion", "plantación",
    "ornamental", "jardineria", "jardinería",
    "reforestacion", "reforestación",
    "viveros", "plantas vivas",
    "silvicultura", "paisajismo",
    "arboles", "árboles",
]


# ─── Municipios prioritarios para la Sabana de Bogotá ───
MUNICIPIOS_PRIORITARIOS = [
    "Bogotá", "Bogota", "Bogotá D.C.", "Bogota D.C.",
    "Cajicá", "Cajica",
    "Chía", "Chia",
    "Cota",
    "Tabio",
    "Tenjo",
    "Tocancipá", "Tocancipa",
    "Sopó", "Sopo",
    "Zipaquirá", "Zipaquira",
    "Funza",
    "Mosquera",
    "Madrid",
]


class SecopClient:
    """Cliente async para SECOP II vía Socrata Open Data API (SODA)."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self.app_token = os.environ.get("SOCRATA_APP_TOKEN")
        if not self.app_token:
            logger.warning(
                "SOCRATA_APP_TOKEN no está seteado. Las queries van a "
                "trabajar contra el rate limit anónimo de datos.gov.co."
            )

    async def __aenter__(self) -> "SecopClient":
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "ViveroOnline-Ingesta/1.0",
        }
        if self.app_token:
            headers["X-App-Token"] = self.app_token.strip()
        self._client = httpx.AsyncClient(timeout=self.timeout, headers=headers)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_procesos(
        self,
        keywords: Optional[list[str]] = None,
        municipios: Optional[list[str]] = None,
        fecha_desde: Optional[str] = None,   # 'YYYY-MM-DD'
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        """Query SECOP II - Procesos de Contratación."""
        if self._client is None:
            raise RuntimeError(
                "SecopClient must be used as async context manager: "
                "async with SecopClient() as c: ..."
            )

        url = f"{BASE_URL}/{DATASET_PROCESOS}.json"
        params: dict[str, Any] = {
            "$limit": min(limit, 50000),
            "$offset": offset,
        }

        where_clauses = []

        if keywords:
            kw_or = " OR ".join([
                f"upper({SECOP_II_DESCRIPCION_COL}) like '%{kw.upper()}%'"
                for kw in keywords
            ])
            where_clauses.append(f"({kw_or})")

        if municipios:
            mun_list = ", ".join([f"'{m}'" for m in municipios])
            where_clauses.append(f"{SECOP_II_CIUDAD_COL} in ({mun_list})")

        if fecha_desde:
            where_clauses.append(
                f"{SECOP_II_FECHA_PUB_COL} >= '{fecha_desde}'"
            )

        if where_clauses:
            params["$where"] = " AND ".join(where_clauses)

        params["$order"] = f"{SECOP_II_FECHA_PUB_COL} DESC"

        logger.info(f"SECOP query: {url} params={params}")
        try:
            resp = await self._client.get(url, params=params)
            # Log response body si hay error (útil para diagnóstico)
            if resp.status_code != 200:
                body_preview = resp.text[:500] if resp.text else "(empty)"
                logger.error(
                    f"SECOP devolvió {resp.status_code}. Body: {body_preview}"
                )
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"SECOP devolvió {len(data)} procesos")
            return data
        except httpx.HTTPError as e:
            logger.error(f"SECOP API error: {e}")
            raise


def extraer_campos(raw: dict) -> dict:
    """Extrae campos relevantes del payload crudo de SECOP con fallbacks."""
    def first(d: dict, *keys, default=None):
        for k in keys:
            if k in d and d[k] not in (None, "", "null"):
                return d[k]
        return default

    return {
        "proceso_id": first(
            raw,
            "id_del_proceso", "id_proceso", "uid", "id_objeto_a_contratar",
        ),
        "entidad": first(
            raw,
            "entidad", "nombre_entidad", "nombre_de_la_entidad",
        ),
        "entidad_nit": first(
            raw,
            "nit_entidad", "nit_de_la_entidad", "id_de_la_entidad",
        ),
        "objeto": first(
            raw,
            "descripci_n_del_procedimiento",
            "descripcion_del_procedimiento",
            "descripci_n_del_proceso",
            "descripcion_del_proceso",
            "objeto_del_contrato",
        ),
        "cuantia_proceso": _to_float(first(
            raw,
            "precio_base",
            "valor_total_de_la_contrataci_n",
            "cuantia_a_contratar",
            "cuantia_proceso",
        )),
        "departamento": first(
            raw,
            "departamento_entidad", "departamento",
        ),
        "ciudad": first(
            raw,
            "ciudad_entidad", "ciudad", "municipio_entidad",
        ),
        "fecha_publicacion": _to_date(first(
            raw,
            "fecha_de_publicacion_del",        # ← columna real (truncada)
            "fecha_de_publicacion_del_proceso",
            "fecha_de_publicacion",
            "fecha_publicacion_proceso",
        )),
        "fecha_recepcion_propuestas": _to_date(first(
            raw,
            "fecha_de_recepcion_de",
            "fecha_recepcion_propuestas",
        )),
        "estado_proceso": first(
            raw,
            "fase",
            "estado_del_procedimiento",
            "estado_de_apertura_del_proceso",
            "estado_proceso",
        ),
        "modalidad_contratacion": first(
            raw,
            "modalidad_de_contratacion",
            "tipo_de_contrato",
        ),
        "url_proceso": _extract_url(first(
            raw,
            "urlproceso", "url_del_proceso",
        )),
        "raw_data": raw,
    }


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_date(v) -> Optional[str]:
    if not v:
        return None
    s = str(v)
    if "T" in s:
        return s.split("T")[0]
    return s[:10] if len(s) >= 10 else None


def _extract_url(v) -> Optional[str]:
    if not v:
        return None
    if isinstance(v, dict):
        return v.get("url") or v.get("u")
    return str(v)
