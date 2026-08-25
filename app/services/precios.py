"""Motor central de cálculo de precios — Modelo comercial matricial.

Este módulo es la ÚNICA fuente de verdad para calcular precios en el marketplace.
Todos los flujos (cotización, checkout, pago, notificaciones) deben pasar por acá.

Regla del Productor v2 (INMUTABLE):
    El viverista SIEMPRE recibe el precio mayorista que publicó.
    ViveroOnline absorbe todos los descuentos comerciales y costos financieros.

Fórmulas:
    precio_vitrina    = precio_mayorista × (1 + markup_categoría_B2C)
    descuento_cliente = (según canal, plazo, categoría — solo B2B ≥ 5 SMLMV)
    precio_final      = precio_vitrina × (1 - descuento_cliente)
    monto_viverista   = precio_mayorista × cantidad  (SIEMPRE)
    monto_viveroonline_bruto = precio_final - monto_viverista
    monto_viveroonline_neto  = bruto - (precio_vitrina × costo_fintech)

Uso típico desde pedidos.py / pagos.py:
    from app.services.precios import calcular_precios_pedido

    resultado = calcular_precios_pedido(
        cliente={"es_guest": True, "cliente_id": 42, "activo": True},
        items=[{"inventario_id": 32, "cantidad": 3}, ...],
        canal="b2c",         # 'b2c' | 'b2b'
        plazo="inmediato",   # 'inmediato' | '30d' | '60d' | '90d'
    )
    resultado["totales"]["precio_final_cliente"]  # a cobrar
    resultado["totales"]["monto_viverista_total"] # a pagar a viveristas
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.config_global import (
    get_markup_categoria,
    get_descuento_b2b,
    get_costo_fintech,
    get_config,
    _normalizar_plazo,
)
from app.services.supabase import admin

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# HELPERS INTERNOS
# ═══════════════════════════════════════════════════════════════════════

def _resolver_categoria(db, inventario_id: int) -> str:
    """Resuelve la categoría efectiva de un SKU usando la función SQL
    get_categoria_producto(inventario_id) que aplica COALESCE:
        inventario.categoria_producto > plantas.categoria_producto > 'plantas_ornamentales'
    """
    try:
        resp = db.rpc("get_categoria_producto", {
            "p_inventario_id": inventario_id
        }).execute()
        if resp.data:
            return str(resp.data)
    except Exception as e:
        logger.warning(
            f"Falló get_categoria_producto({inventario_id}): {e}. "
            f"Fallback a query manual."
        )

    # Fallback: query manual con SELECT + LEFT JOIN
    try:
        inv = db.table("inventario").select(
            "categoria_producto, planta_id"
        ).eq("inventario_id", inventario_id).limit(1).execute()
        if not inv.data:
            return "plantas_ornamentales"
        row = inv.data[0]
        if row.get("categoria_producto"):
            return row["categoria_producto"]
        planta_id = row.get("planta_id")
        if planta_id:
            pl = db.table("plantas").select(
                "categoria_producto"
            ).eq("planta_id", planta_id).limit(1).execute()
            if pl.data and pl.data[0].get("categoria_producto"):
                return pl.data[0]["categoria_producto"]
    except Exception as e:
        logger.error(f"Error resolviendo categoría de inv {inventario_id}: {e}")

    return "plantas_ornamentales"


def _obtener_precio_mayorista(db, inventario_id: int) -> float:
    """Lee el precio_mayorista actual del SKU. Retorna 0.0 si no lo encuentra."""
    try:
        resp = db.table("inventario").select(
            "precio_mayorista"
        ).eq("inventario_id", inventario_id).limit(1).execute()
        if resp.data and resp.data[0].get("precio_mayorista") is not None:
            return float(resp.data[0]["precio_mayorista"])
    except Exception as e:
        logger.error(f"Error leyendo precio_mayorista de inv {inventario_id}: {e}")
    return 0.0


def _determinar_canal_y_descuento(
    cliente: dict[str, Any],
    total_mayorista: float,
) -> tuple[str, bool]:
    """Decide si aplica descuento B2B ≥ 5 SMLMV.

    Args:
        cliente: dict con campos como es_guest, tipo_cliente, activo, etc.
        total_mayorista: suma de precios mayoristas × cantidades

    Returns:
        (canal, aplica_descuento_b2b)
        canal ∈ {'b2c', 'b2b'}
    """
    # Detectar canal
    es_guest = bool(cliente.get("es_guest", False))
    if es_guest:
        return ("b2c", False)

    # B2B registrado: verificar si supera umbral SMLMV
    smlmv = float(get_config("smlmv_actual", default=1_750_905))
    umbral_smlmv = int(get_config("umbral_descuento_b2b_smlmv", default=5))
    umbral_pesos = smlmv * umbral_smlmv

    # El descuento se aplica sobre el precio de VITRINA total.
    # Necesitamos aproximar el vitrina total con el promedio para el umbral.
    # Estrategia conservadora: usamos el mayorista como piso — si el mayorista
    # ya supera el umbral, la vitrina también lo hace.
    aplica = total_mayorista >= umbral_pesos

    return ("b2b", aplica)


# ═══════════════════════════════════════════════════════════════════════
# FUNCIÓN PÚBLICA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════

def calcular_precios_pedido(
    cliente: dict[str, Any],
    items: list[dict[str, Any]],
    plazo: str | int = "inmediato",
    forzar_canal: Optional[str] = None,
) -> dict[str, Any]:
    """Calcula el desglose completo de precios de un pedido.

    Los items pueden ser de categorías distintas — cada uno se calcula
    independientemente y luego se suma.

    Args:
        cliente: dict con al menos {es_guest, cliente_id}. Puede tener más campos.
        items: lista de dicts con {inventario_id, cantidad}. Puede tener más.
        plazo: 'inmediato' | '30d' | '60d' | '90d' (default 'inmediato')
        forzar_canal: 'b2c' o 'b2b' para override manual. Si None, se detecta.

    Returns:
        dict con estructura:
        {
            "canal": "b2c" | "b2b",
            "plazo": "inmediato" | ...,
            "aplica_descuento_b2b": bool,
            "items_desglose": [
                {
                    "inventario_id": int,
                    "cantidad": int,
                    "categoria": str,
                    "precio_mayorista_unitario": float,
                    "markup_categoria": float,
                    "descuento_aplicado": float,
                    "precio_vitrina_unitario": float,
                    "precio_final_unitario": float,
                    "subtotal_mayorista": float,   # cantidad × mayorista
                    "subtotal_vitrina": float,      # cantidad × vitrina
                    "subtotal_final": float,        # cantidad × precio_final (lo que paga cliente)
                    "monto_viverista": float,       # SIEMPRE = subtotal_mayorista
                    "monto_viveroonline_bruto": float,
                    "monto_viveroonline_neto": float,
                    "costo_fintech": float,
                },
                ...
            ],
            "totales": {
                "cantidad_items": int,
                "cantidad_unidades": int,
                "precio_mayorista_total": float,     # suma mayoristas
                "precio_vitrina_total": float,        # suma vitrina
                "precio_final_cliente": float,        # LO QUE PAGA EL CLIENTE
                "monto_viverista_total": float,       # LO QUE COBRA VIVERISTA(S)
                "monto_viveroonline_bruto_total": float,
                "monto_viveroonline_neto_total": float,
                "costo_fintech_total": float,
                "porcentaje_comision_efectivo": float, # neta / vitrina × 100
            },
            "config_snapshot": {
                "smlmv_actual": float,
                "umbral_smlmv": int,
                "umbral_pesos": float,
                "fintech_activa": bool,
            },
        }
    """
    db = admin()

    # ── 1. Pre-cálculo mayorista para decidir canal ──────────────────────
    items_pre = []
    total_mayorista_pre = 0.0
    for item in items:
        inv_id = item.get("inventario_id")
        cant = int(item.get("cantidad", 0))
        if not inv_id or cant <= 0:
            continue

        # Preferir precio_mayorista del item si viene incluido (ahorra query)
        pu = item.get("precio_unitario")
        if pu is None:
            pu = _obtener_precio_mayorista(db, inv_id)
        pu = float(pu)

        subtotal = pu * cant
        total_mayorista_pre += subtotal
        items_pre.append({
            "inventario_id": inv_id,
            "cantidad": cant,
            "precio_mayorista_unitario": pu,
            "subtotal_mayorista": subtotal,
        })

    # ── 2. Determinar canal y si aplica descuento ────────────────────────
    if forzar_canal in ("b2c", "b2b"):
        canal = forzar_canal
        if canal == "b2b":
            smlmv = float(get_config("smlmv_actual", default=1_750_905))
            umbral_smlmv = int(get_config("umbral_descuento_b2b_smlmv", default=5))
            aplica = total_mayorista_pre >= (smlmv * umbral_smlmv)
        else:
            aplica = False
    else:
        canal, aplica = _determinar_canal_y_descuento(cliente, total_mayorista_pre)

    plazo_norm = _normalizar_plazo(plazo)
    fintech_activa = bool(get_config("fintech_activa", default=False))

    # Si la fintech está inactiva, forzar plazo inmediato (los otros no aplican)
    if plazo_norm != "inmediato" and not fintech_activa:
        logger.info(
            f"Plazo {plazo_norm} solicitado pero fintech_activa=false. "
            f"Forzando 'inmediato'."
        )
        plazo_norm = "inmediato"

    # ── 3. Calcular desglose item por item ───────────────────────────────
    items_desglose = []
    total_vitrina = 0.0
    total_final = 0.0
    total_viverista = 0.0
    total_bruto_vo = 0.0
    total_neto_vo = 0.0
    total_costo_fintech = 0.0
    total_unidades = 0

    costo_fintech_pct = get_costo_fintech(plazo_norm)

    for item in items_pre:
        inv_id = item["inventario_id"]
        cant = item["cantidad"]
        pu_mayorista = item["precio_mayorista_unitario"]

        # Resolver categoría
        categoria = _resolver_categoria(db, inv_id)

        # Markup y precio vitrina
        markup = get_markup_categoria(categoria)
        pu_vitrina = pu_mayorista * (1 + markup)
        subtotal_vitrina = pu_vitrina * cant

        # Descuento aplicable
        if canal == "b2b" and aplica:
            descuento = get_descuento_b2b(categoria, plazo_norm)
        else:
            descuento = 0.0

        pu_final = pu_vitrina * (1 - descuento)
        subtotal_final = pu_final * cant
        subtotal_mayorista = item["subtotal_mayorista"]

        # Regla del Productor: viverista recibe siempre el mayorista
        monto_viverista = subtotal_mayorista

        # Bruto = lo que cobra VO antes de fintech
        monto_bruto = subtotal_final - monto_viverista

        # Costo fintech se calcula sobre el vitrina (base)
        costo_fintech_item = subtotal_vitrina * costo_fintech_pct

        # Neto = bruto - costo fintech
        monto_neto = monto_bruto - costo_fintech_item

        items_desglose.append({
            "inventario_id": inv_id,
            "cantidad": cant,
            "categoria": categoria,
            "precio_mayorista_unitario": round(pu_mayorista, 2),
            "markup_categoria": markup,
            "descuento_aplicado": descuento,
            "precio_vitrina_unitario": round(pu_vitrina, 2),
            "precio_final_unitario": round(pu_final, 2),
            "subtotal_mayorista": round(subtotal_mayorista, 2),
            "subtotal_vitrina": round(subtotal_vitrina, 2),
            "subtotal_final": round(subtotal_final, 2),
            "monto_viverista": round(monto_viverista, 2),
            "monto_viveroonline_bruto": round(monto_bruto, 2),
            "monto_viveroonline_neto": round(monto_neto, 2),
            "costo_fintech": round(costo_fintech_item, 2),
        })

        total_vitrina += subtotal_vitrina
        total_final += subtotal_final
        total_viverista += monto_viverista
        total_bruto_vo += monto_bruto
        total_neto_vo += monto_neto
        total_costo_fintech += costo_fintech_item
        total_unidades += cant

    # ── 4. Totales ───────────────────────────────────────────────────────
    pct_efectivo = 0.0
    if total_vitrina > 0:
        pct_efectivo = (total_neto_vo / total_vitrina) * 100

    smlmv = float(get_config("smlmv_actual", default=1_750_905))
    umbral_smlmv = int(get_config("umbral_descuento_b2b_smlmv", default=5))

    return {
        "canal": canal,
        "plazo": plazo_norm,
        "aplica_descuento_b2b": aplica and canal == "b2b",
        "items_desglose": items_desglose,
        "totales": {
            "cantidad_items": len(items_desglose),
            "cantidad_unidades": total_unidades,
            "precio_mayorista_total": round(total_mayorista_pre, 2),
            "precio_vitrina_total": round(total_vitrina, 2),
            "precio_final_cliente": round(total_final, 2),
            "monto_viverista_total": round(total_viverista, 2),
            "monto_viveroonline_bruto_total": round(total_bruto_vo, 2),
            "monto_viveroonline_neto_total": round(total_neto_vo, 2),
            "costo_fintech_total": round(total_costo_fintech, 2),
            "porcentaje_comision_efectivo": round(pct_efectivo, 2),
        },
        "config_snapshot": {
            "smlmv_actual": smlmv,
            "umbral_smlmv": umbral_smlmv,
            "umbral_pesos": smlmv * umbral_smlmv,
            "fintech_activa": fintech_activa,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# HELPERS DE COMPATIBILIDAD hacia atrás
# ═══════════════════════════════════════════════════════════════════════

def calcular_precio_comprador_legacy(total_mayorista: float) -> float:
    """Compatibilidad con el modelo viejo (MARKUP_PLATAFORMA = 0.20).

    ADVERTENCIA: esta función NO usa el nuevo modelo matricial por categoría.
    Solo existe para mantener compatibilidad temporal con código legacy que
    calculaba `total × 1.20` sin desglosar por SKU. Preferir siempre
    calcular_precios_pedido() que respeta la Regla del Productor v2.

    Usa el markup por defecto de 'plantas_ornamentales' (20%) como aproximación
    cuando NO se conoce la categoría de los items.
    """
    markup_default = get_markup_categoria("plantas_ornamentales")
    return round(total_mayorista * (1 + markup_default))
