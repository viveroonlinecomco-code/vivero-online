"""Acceso centralizado a la tabla configuracion_global con caché en memoria.

Diseño:
- Caché por 60 seg para evitar golpear Supabase en cada request
- Conversión automática de tipos según tipo_dato de la clave
- Fail-safe: si Supabase falla, retorna default y loguea
- Thread-safe: usa lock para el caché

Uso típico:
    from app.services.config_global import get_config

    comision = get_config("comision_plataforma", default=0.20)
    fintech_activa = get_config("fintech_activa", default=False)
    umbral = get_config("umbral_descuento_b2b_smlmv", default=5)

Cuando cambies un valor desde el dashboard admin, la caché se actualiza
en máximo 60 seg. Para forzar recarga inmediata, llamá invalidate_cache().
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.services.supabase import admin

logger = logging.getLogger(__name__)

# ─── Caché en memoria ─────────────────────────────────────────────────────────
_CACHE: dict[str, tuple[Any, float]] = {}  # clave -> (valor_convertido, timestamp)
_CACHE_TTL_SECONDS = 60
_CACHE_LOCK = threading.Lock()


def _convertir_valor(valor_str: str, tipo_dato: str) -> Any:
    """Convierte el valor string de Supabase al tipo Python correcto.

    Tipos soportados: string, number, boolean, json.
    Si falla la conversión, retorna el string original (fail-safe).
    """
    if valor_str is None:
        return None

    tipo = (tipo_dato or "string").lower()

    try:
        if tipo == "number":
            # Puede ser int o float — intentamos int primero
            if "." in valor_str:
                return float(valor_str)
            return int(valor_str)

        if tipo == "boolean":
            return valor_str.lower().strip() in ("true", "1", "yes", "sí", "si")

        if tipo == "json":
            import json
            return json.loads(valor_str)

        # string por defecto
        return valor_str

    except Exception as e:
        logger.warning(
            f"Error convirtiendo valor '{valor_str}' como {tipo}: {e}. "
            f"Retornando string original."
        )
        return valor_str


def get_config(clave: str, default: Any = None) -> Any:
    """Lee una clave de configuracion_global con caché de 60 seg.

    Args:
        clave: nombre de la clave en configuracion_global
        default: valor a retornar si la clave no existe o Supabase falla

    Returns:
        Valor convertido al tipo correcto, o default si algo sale mal.
        NUNCA levanta excepción — siempre retorna algo usable.
    """
    now = time.time()

    # Chequear caché
    with _CACHE_LOCK:
        cached = _CACHE.get(clave)
        if cached is not None:
            valor, timestamp = cached
            if now - timestamp < _CACHE_TTL_SECONDS:
                return valor

    # Cargar desde Supabase
    try:
        db = admin()
        resp = db.table("configuracion_global").select(
            "valor, tipo_dato"
        ).eq("clave", clave).limit(1).execute()

        if not resp.data:
            # Clave no existe: retornar default sin cachear
            logger.warning(f"Clave '{clave}' no existe en configuracion_global. Usando default: {default}")
            return default

        row = resp.data[0]
        valor_convertido = _convertir_valor(row["valor"], row["tipo_dato"])

        # Guardar en caché
        with _CACHE_LOCK:
            _CACHE[clave] = (valor_convertido, now)

        return valor_convertido

    except Exception as e:
        logger.error(f"Error leyendo config '{clave}': {e}. Usando default: {default}")
        return default


def get_config_multi(claves: list[str]) -> dict[str, Any]:
    """Lee múltiples claves en una sola query. Más eficiente que llamar
    get_config N veces cuando necesitás varias juntas.

    Args:
        claves: lista de nombres de claves

    Returns:
        dict {clave: valor_convertido}. Claves faltantes NO aparecen en el dict.
    """
    if not claves:
        return {}

    now = time.time()
    resultado: dict[str, Any] = {}
    claves_a_cargar = []

    # Chequear caché para cada clave
    with _CACHE_LOCK:
        for clave in claves:
            cached = _CACHE.get(clave)
            if cached is not None:
                valor, timestamp = cached
                if now - timestamp < _CACHE_TTL_SECONDS:
                    resultado[clave] = valor
                    continue
            claves_a_cargar.append(clave)

    if not claves_a_cargar:
        return resultado

    # Cargar las faltantes en una sola query
    try:
        db = admin()
        resp = db.table("configuracion_global").select(
            "clave, valor, tipo_dato"
        ).in_("clave", claves_a_cargar).execute()

        for row in resp.data or []:
            valor_convertido = _convertir_valor(row["valor"], row["tipo_dato"])
            resultado[row["clave"]] = valor_convertido

            # Actualizar caché
            with _CACHE_LOCK:
                _CACHE[row["clave"]] = (valor_convertido, now)

    except Exception as e:
        logger.error(f"Error leyendo config múltiple {claves_a_cargar}: {e}")

    return resultado


def invalidate_cache(clave: str | None = None) -> None:
    """Invalida la caché.

    Args:
        clave: si se pasa, invalida solo esa clave. Si es None, invalida todo.

    Uso típico: llamar después de actualizar una clave desde el admin dashboard
    para que los cambios se reflejen inmediatamente.
    """
    with _CACHE_LOCK:
        if clave is None:
            _CACHE.clear()
            logger.info("Caché de configuracion_global invalidada completamente")
        else:
            _CACHE.pop(clave, None)
            logger.info(f"Caché de configuracion_global invalidada para: {clave}")


def cache_stats() -> dict[str, Any]:
    """Retorna estadísticas del caché para diagnóstico."""
    with _CACHE_LOCK:
        return {
            "keys_cached": len(_CACHE),
            "keys": list(_CACHE.keys()),
            "ttl_seconds": _CACHE_TTL_SECONDS,
        }
