"""Cliente HTTP para la API pública de SECOP II en datos.gov.co (Socrata).

API pública (sin auth) que soporta SoQL (Socrata Query Language).
Docs: https://dev.socrata.com/foundry/www.datos.gov.co/p6dx-8zbt

Datasets relevantes:
- p6dx-8zbt: SECOP II - Procesos de Contratación  (target Sprint 1)
- jbjy-vk9h: SECOP II - Contratos Electrónicos
- b6m4-qgqv: SECOP II - PAA Encabezado (Plan Anual de Adquisiciones)
"""
from __future__ import annotations
import logging
from typing import Optional, Any

import httpx


logger = logging.getLogger(__name__)


# ─── IDs de datasets de Socrata ───
DATASET_PROCESOS = "p6dx-8zbt"
DATASET_CONTRATOS = "jbjy-vk9h"
DATASET_PAA = "b6m4-qgqv"

BASE_URL = "https://www.datos.gov.co/resource"
DEFAULT_TIMEOUT = 30.0


# ─── Keywords para filtrar procesos relevantes a plantas ───
# Usadas en SoQL $where con LIKE case-insensitive
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
# Variantes con/sin tilde para matching robusto
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
    """Cliente async para SECOP II vía Socrata Open Data API (SODA).

    Uso:
        async with SecopClient() as client:
            procesos = await client.fetch_procesos(
                keywords=KEYWORDS_PLANTAS,
                municipios=MUNICIPIOS_PRIORITARIOS,
                fecha_desde="2025-12-01",
                limit=200,
            )
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "SecopClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
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
        """Query SECOP II - Procesos de Contratación.

        Filtros opcionales — todos se aplican como AND:
        - keywords: matchea cualquier keyword en el objeto (OR interno)
        - municipios: filtra por ciudad de la entidad (OR interno)
        - fecha_desde: solo procesos publicados desde esta fecha

        Devuelve lista de dicts con el payload crudo. Los nombres de
        campos pueden variar; el caller debe usar `extraer_campos()`
        para normalizar.
        """
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
                f"upper(objeto_del_contrato) like '%{kw.upper()}%'"
                for kw in keywords
            ])
            where_clauses.append(f"({kw_or})")

        if municipios:
            mun_list = ", ".join([f"'{m}'" for m in municipios])
            where_clauses.append(f"ciudad_entidad in ({mun_list})")

        if fecha_desde:
            where_clauses.append(
                f"fecha_de_publicacion_del_proceso >= '{fecha_desde}'"
            )

        if where_clauses:
            params["$where"] = " AND ".join(where_clauses)

        params["$order"] = "fecha_de_publicacion_del_proceso DESC"

        logger.info(f"SECOP query: {url} params={params}")
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"SECOP devolvió {len(data)} procesos")
            return data
        except httpx.HTTPError as e:
            logger.error(f"SECOP API error: {e}")
            raise


def extraer_campos(raw: dict) -> dict:
    """Extrae campos relevantes del payload crudo de SECOP con fallbacks.

    Los nombres de campos en SECOP no son 100% estables. Esta función
    intenta múltiples nombres comunes para cada campo. El `raw_data`
    completo se preserva igual en el dict devuelto.

    Devuelve dict listo para insertar/upsert en `secop_procesos`.
    """
    def first(d: dict, *keys, default=None):
        """Primer key existente con valor no-None."""
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
            "objeto_del_contrato",
            "descripci_n_del_procedimiento",
            "descripcion_del_procedimiento",
            "descripci_n_del_proceso",
            "descripcion_del_proceso",
        ),
        "cuantia_proceso": _to_float(first(
            raw,
            "cuantia_a_contratar",
            "valor_total_de_la_contrataci_n",
            "cuantia_proceso",
            "precio_base",
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
            "fecha_de_publicacion_del_proceso",
            "fecha_de_publicacion_del",
            "fecha_publicacion_proceso",
        )),
        "fecha_recepcion_propuestas": _to_date(first(
            raw,
            "fecha_de_recepcion_de",
            "fecha_recepcion_propuestas",
        )),
        "estado_proceso": first(
            raw,
            "estado_del_procedimiento",
            "estado_de_apertura_del_proceso",
            "estado_proceso",
            "fase",
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
    """Convierte timestamps de Socrata a YYYY-MM-DD."""
    if not v:
        return None
    s = str(v)
    # Formato común de Socrata: '2024-09-30T00:00:00.000'
    if "T" in s:
        return s.split("T")[0]
    return s[:10] if len(s) >= 10 else None


def _extract_url(v) -> Optional[str]:
    """Socrata URL fields pueden ser strings o dicts {url, description}."""
    if not v:
        return None
    if isinstance(v, dict):
        return v.get("url") or v.get("u")
    return str(v)
