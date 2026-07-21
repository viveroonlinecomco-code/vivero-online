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

═══════════════════════════════════════════════════════════════════════
EXTENSIÓN Fase 4 (21 jul 2026) — Motor comercial matricial:
    from app.services.config_global import (
        get_matriz_comercial,
        get_markup_categoria,
        get_descuento_b2b,
        get_costo_fintech,
        get_comision_bruta,
        get_comision_neta,
    )

Todas leen la clave 'matriz_comercial' (tipo_dato=json) de configuracion_global.
═══════════════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════════════
# EXTENSIÓN FASE 4 — Motor comercial matricial
# ═══════════════════════════════════════════════════════════════════════

# Categorías válidas del modelo comercial
CATEGORIAS_VALIDAS = frozenset({
    "plantas_ornamentales",
    "arboles",
    "materas",
    "sustrato",
    "accesorios",
    "otros",
})

# Plazos válidos
PLAZOS_VALIDOS = frozenset({"inmediato", "30d", "60d", "90d"})

# Matriz DEFAULT — se usa si la clave 'matriz_comercial' no existe en BD o falla.
# Nunca deberíamos llegar acá si la migration corrió bien, pero es fail-safe.
_MATRIZ_DEFAULT = {
    "markup_b2c": {
        "plantas_ornamentales": 0.20,
        "arboles": 0.20,
        "materas": 0.25,
        "sustrato": 0.17,
        "accesorios": 0.25,
        "otros": 0.20,
    },
    "descuento_b2b_inmediato": {
        "plantas_ornamentales": 0.12,
        "arboles": 0.12,
        "materas": 0.10,
        "sustrato": 0.09,
        "accesorios": 0.14,
        "otros": 0.12,
    },
    "descuento_b2b_30d": {
        "plantas_ornamentales": 0.09,
        "arboles": 0.09,
        "materas": 0.07,
        "sustrato": 0.06,
        "accesorios": 0.11,
        "otros": 0.09,
    },
    "descuento_b2b_60d": {
        "plantas_ornamentales": 0.06,
        "arboles": 0.06,
        "materas": 0.04,
        "sustrato": 0.03,
        "accesorios": 0.08,
        "otros": 0.06,
    },
    "descuento_b2b_90d": {
        "plantas_ornamentales": 0.03,
        "arboles": 0.03,
        "materas": 0.01,
        "sustrato": 0.00,
        "accesorios": 0.05,
        "otros": 0.03,
    },
    "costo_fintech": {
        "inmediato": 0.00,
        "30d": 0.03,
        "60d": 0.06,
        "90d": 0.09,
    },
}


def _normalizar_categoria(categoria: str | None) -> str:
    """Devuelve una categoría válida. Fallback a 'otros' si es None o inválida."""
    if not categoria:
        return "otros"
    cat = str(categoria).strip().lower()
    if cat not in CATEGORIAS_VALIDAS:
        logger.warning(
            f"Categoría desconocida '{categoria}'. Usando 'otros' como fallback."
        )
        return "otros"
    return cat


def _normalizar_plazo(plazo: str | int | None) -> str:
    """Devuelve un plazo válido. Acepta int (0/30/60/90) o string ('inmediato'/'30d'/...).
    Fallback a 'inmediato'."""
    if plazo is None:
        return "inmediato"

    if isinstance(plazo, int):
        return {0: "inmediato", 30: "30d", 60: "60d", 90: "90d"}.get(plazo, "inmediato")

    p = str(plazo).strip().lower()
    if p in PLAZOS_VALIDOS:
        return p
    # Aceptar variantes comunes
    if p in ("0", "0d", "contado", "cash"):
        return "inmediato"
    if p in ("30", "30 dias", "30 días"):
        return "30d"
    if p in ("60", "60 dias", "60 días"):
        return "60d"
    if p in ("90", "90 dias", "90 días"):
        return "90d"

    logger.warning(f"Plazo desconocido '{plazo}'. Usando 'inmediato' como fallback.")
    return "inmediato"


def get_matriz_comercial() -> dict[str, Any]:
    """Lee la matriz comercial completa desde configuracion_global.

    Returns:
        dict con las 6 sub-tablas: markup_b2c, descuento_b2b_inmediato,
        descuento_b2b_30d, descuento_b2b_60d, descuento_b2b_90d, costo_fintech.
        Si falla la lectura, retorna _MATRIZ_DEFAULT (fail-safe).
    """
    matriz = get_config("matriz_comercial", default=_MATRIZ_DEFAULT)

    # Validar estructura mínima — si viene incompleta, fusionar con default
    if not isinstance(matriz, dict):
        logger.error(
            f"matriz_comercial no es dict (tipo: {type(matriz).__name__}). "
            f"Usando default."
        )
        return _MATRIZ_DEFAULT

    for seccion, tabla_default in _MATRIZ_DEFAULT.items():
        if seccion not in matriz or not isinstance(matriz[seccion], dict):
            logger.warning(
                f"matriz_comercial sin sección '{seccion}'. Rellenando con default."
            )
            matriz[seccion] = tabla_default

    return matriz


def get_markup_categoria(categoria: str) -> float:
    """Devuelve el markup B2C aplicado a una categoría.

    Args:
        categoria: una de CATEGORIAS_VALIDAS. Fallback a 'otros' si inválida.

    Returns:
        float (ej: 0.20 para plantas_ornamentales, 0.25 para materas).
    """
    cat = _normalizar_categoria(categoria)
    matriz = get_matriz_comercial()
    markup = matriz.get("markup_b2c", {}).get(cat)

    if markup is None:
        markup = _MATRIZ_DEFAULT["markup_b2c"].get(cat, 0.20)
        logger.warning(f"Markup faltante para '{cat}'. Usando default {markup}.")

    return float(markup)


def get_descuento_b2b(categoria: str, plazo: str | int) -> float:
    """Devuelve el descuento B2B para una categoría y plazo específico.

    Solo aplica cuando cliente es B2B registrado Y compra >= 5 SMLMV.
    El caller es responsable de verificar esas condiciones antes de llamar.

    Args:
        categoria: una de CATEGORIAS_VALIDAS
        plazo: 'inmediato' | '30d' | '60d' | '90d' (acepta también int 0/30/60/90)

    Returns:
        float (ej: 0.10 para materas inmediato = 10% descuento sobre vitrina).
    """
    cat = _normalizar_categoria(categoria)
    p = _normalizar_plazo(plazo)

    clave_seccion = f"descuento_b2b_{p}"
    matriz = get_matriz_comercial()
    descuento = matriz.get(clave_seccion, {}).get(cat)

    if descuento is None:
        descuento = _MATRIZ_DEFAULT[clave_seccion].get(cat, 0.0)
        logger.warning(
            f"Descuento faltante para ({cat}, {p}). Usando default {descuento}."
        )

    return float(descuento)


def get_costo_fintech(plazo: str | int) -> float:
    """Devuelve el costo estimado de la fintech para un plazo.

    Costos proyectados (hasta contratar fintech real):
        inmediato=0, 30d=3%, 60d=6%, 90d=9%

    Args:
        plazo: 'inmediato' | '30d' | '60d' | '90d' (acepta también int)

    Returns:
        float (ej: 0.03 para 30 días).
    """
    p = _normalizar_plazo(plazo)
    matriz = get_matriz_comercial()
    costo = matriz.get("costo_fintech", {}).get(p)

    if costo is None:
        costo = _MATRIZ_DEFAULT["costo_fintech"].get(p, 0.0)
        logger.warning(f"Costo fintech faltante para '{p}'. Usando default {costo}.")

    return float(costo)


def get_comision_bruta(categoria: str, plazo: str | int) -> float:
    """Devuelve la comisión BRUTA de ViveroOnline (markup - descuento).

    Es lo que ViveroOnline cobra al cliente antes de restar el costo fintech.
    Fórmula: comision_bruta = markup_b2c - descuento_b2b

    Ejemplo:
        materas inmediato: 0.25 - 0.10 = 0.15 (15%)
        materas 30d:       0.25 - 0.07 = 0.18 (18%)

    Args:
        categoria: una de CATEGORIAS_VALIDAS
        plazo: 'inmediato' | '30d' | '60d' | '90d'

    Returns:
        float con la comisión bruta expresada como fracción.
    """
    markup = get_markup_categoria(categoria)
    descuento = get_descuento_b2b(categoria, plazo)
    bruta = markup - descuento
    # Piso 0 para evitar comisiones negativas por bug de configuración
    return max(0.0, bruta)


def get_comision_neta(categoria: str, plazo: str | int) -> float:
    """Devuelve la comisión NETA de ViveroOnline (bruta - costo fintech).

    Es el margen real que le queda a ViveroOnline después de pagarle a la fintech.
    Fórmula: comision_neta = comision_bruta - costo_fintech

    Ejemplo:
        materas inmediato: 0.15 - 0.00 = 0.15 (15% neto)
        materas 30d:       0.18 - 0.03 = 0.15 (15% neto — MISMO margen)
        materas 90d:       0.24 - 0.09 = 0.15 (15% neto — MISMO margen)

    Args:
        categoria: una de CATEGORIAS_VALIDAS
        plazo: 'inmediato' | '30d' | '60d' | '90d'

    Returns:
        float con la comisión neta expresada como fracción.
    """
    bruta = get_comision_bruta(categoria, plazo)
    costo_fintech = get_costo_fintech(plazo)
    neta = bruta - costo_fintech
    # Piso 0 para evitar netos negativos por bug de configuración
    return max(0.0, neta)
