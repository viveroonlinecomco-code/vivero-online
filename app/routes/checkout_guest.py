"""Checkout guest B2C — Fase 10.2

Endpoints públicos SIN autenticación para el flujo de compra guest.
Este archivo solo cubre validación pre-checkout. Fase 10.3 agrega create-order
+ webhook ePayco.

Endpoints:
- POST /api/public/checkout-guest/validate-cart  → re-valida items del carrito
- POST /api/public/checkout-guest/calcular-flete → calcula flete por ciudad

Reglas B2C guest:
- Solo tier S+M
- Máx 10 unidades por producto
- Máx 120 plantas total
- Precios via motor matricial por categoría
"""
from __future__ import annotations
from typing import Optional
from decimal import Decimal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.supabase import admin
from app.services.config_global import get_matriz_comercial

router = APIRouter(prefix="/api/public/checkout-guest", tags=["checkout-guest"])


# ═══════════════════════════════════════════════════════════
# Schemas
# ═══════════════════════════════════════════════════════════

class CartItem(BaseModel):
    inventario_id: int
    cantidad: int = Field(ge=1, le=10, description="Máximo 10 por producto")


class ValidateCartRequest(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=60)


class CalcularFleteRequest(BaseModel):
    ciudad: str = Field(min_length=2, max_length=100)
    items: list[CartItem] = Field(min_length=1)


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

_TIER_ORDER = {"S": 1, "M": 2, "L": 3, "XL": 4}

# Tarifas base por tier + zona (COP)
# Zona 1: Sabana cercana (Chía, Cajicá, Cota, Tenjo, Tabio, Sopó, Bogotá Norte)
# Zona 2: Sabana lejana (Zipaquirá, La Calera, Facatativá, Madrid, Mosquera, Funza, Tocancipá, Bogotá Centro, Bogotá Sur)
_ZONA_1 = {"Chía", "Cajicá", "Cota", "Tenjo", "Tabio", "Sopó", "Bogotá Norte"}
_ZONA_2 = {"Zipaquirá", "La Calera", "Facatativá", "Madrid", "Mosquera",
           "Funza", "Tocancipá", "Bogotá Centro", "Bogotá Sur", "Otro"}

# Fallback tarifas (misma estructura que _calcular_flete_stub en el sistema legacy)
_FLETE_STUB = {
    ("S", 1): 25_000, ("S", 2): 40_000,
    ("M", 1): 35_000, ("M", 2): 55_000,
    ("L", 1): 65_000, ("L", 2): 95_000,
    ("XL", 1): 95_000, ("XL", 2): 140_000,
}


def _resolver_zona(ciudad: str) -> int:
    """Devuelve 1 (cercana) o 2 (lejana). Fallback: 2."""
    if ciudad in _ZONA_1:
        return 1
    return 2


def _tier_mayor(tiers: list[str]) -> str:
    """De una lista de tiers, devuelve el mayor (el flete se cobra por el mayor)."""
    tiers_validos = [t for t in tiers if t in _TIER_ORDER]
    if not tiers_validos:
        return "M"
    return max(tiers_validos, key=lambda t: _TIER_ORDER[t])


def _calcular_flete_por_tarifa(db, ciudad: str, tier: str) -> int:
    """Consulta tabla tarifas_logistica si existe. Fallback: stub."""
    try:
        resp = db.table("tarifas_logistica").select(
            "tarifa_cop"
        ).eq("ciudad", ciudad).eq("tier", tier).limit(1).execute()
        if resp.data and resp.data[0].get("tarifa_cop"):
            return int(resp.data[0]["tarifa_cop"])
    except Exception:
        pass
    # Fallback stub
    zona = _resolver_zona(ciudad)
    return _FLETE_STUB.get((tier, zona), 55_000)


def _resolver_categorias_batch(db, inventario_ids: list[int]) -> dict[int, str]:
    """Batch para múltiples items (copia de marketplace.py para no duplicar imports)."""
    if not inventario_ids:
        return {}
    resp = db.table("inventario").select(
        "inventario_id, categoria_producto, plantas(categoria_producto)"
    ).in_("inventario_id", inventario_ids).execute()
    resultado = {}
    for row in resp.data or []:
        override = row.get("categoria_producto")
        default = (row.get("plantas") or {}).get("categoria_producto")
        resultado[row["inventario_id"]] = override or default or "plantas_ornamentales"
    return resultado


# ═══════════════════════════════════════════════════════════
# Endpoint 1: VALIDATE CART
# ═══════════════════════════════════════════════════════════

@router.post("/validate-cart")
async def validate_cart(req: ValidateCartRequest):
    """Re-valida items del carrito guest contra la BD.

    Casos que valida:
    - Item existe
    - Item es tier S o M (guest no puede comprar L/XL)
    - Item está disponible
    - Stock >= cantidad pedida
    - Cantidad <= 10 por producto
    - Total <= 120 plantas
    - Recalcula precio_comprador (por si cambió el markup en la matriz)

    Devuelve items válidos con precios actualizados, o lista de errores.
    """
    db = admin()

    # Límite total 120 plantas
    total_unidades = sum(it.cantidad for it in req.items)
    if total_unidades > 120:
        raise HTTPException(400, detail={
            "error": "limite_total",
            "mensaje": f"Máximo 120 plantas por compra (tenés {total_unidades}). Registrate como empresa para más.",
            "total_actual": total_unidades,
            "limite": 120,
        })

    inv_ids = [it.inventario_id for it in req.items]

    # Batch: datos completos del inventario
    inv_resp = db.table("inventario").select(
        "inventario_id, precio_mayorista, stock, estado_planta, "
        "logistics_tier, foto_ia_url, categoria_producto, "
        "plantas(nombre_comun, categoria_producto), "
        "viveros(nombre_vivero, ciudad)"
    ).in_("inventario_id", inv_ids).execute()
    inv_map = {r["inventario_id"]: r for r in (inv_resp.data or [])}

    # Precargar matriz
    matriz = get_matriz_comercial()
    markups_b2c = matriz.get("markup_b2c", {})

    items_validados = []
    errores = []
    total_plantas = 0
    total_cop = 0
    tiers_presentes = []

    for req_item in req.items:
        inv = inv_map.get(req_item.inventario_id)

        if not inv:
            errores.append({
                "inventario_id": req_item.inventario_id,
                "error": "no_existe",
                "mensaje": "Producto ya no está disponible",
            })
            continue

        # Guardrail: guest solo S+M
        tier = inv.get("logistics_tier", "M")
        if tier not in ("S", "M"):
            errores.append({
                "inventario_id": req_item.inventario_id,
                "error": "tier_no_permitido",
                "mensaje": "Este producto requiere cuenta empresa",
            })
            continue

        if inv.get("estado_planta") != "disponible":
            errores.append({
                "inventario_id": req_item.inventario_id,
                "error": "no_disponible",
                "mensaje": "Producto sin stock",
            })
            continue

        stock = inv.get("stock") or 0
        if stock < req_item.cantidad:
            errores.append({
                "inventario_id": req_item.inventario_id,
                "error": "stock_insuficiente",
                "mensaje": f"Solo hay {stock} disponibles",
                "stock_disponible": stock,
                "cantidad_solicitada": req_item.cantidad,
            })
            continue

        # Calcular precio comprador via motor matricial
        override_inv = inv.get("categoria_producto")
        default_planta = (inv.get("plantas") or {}).get("categoria_producto")
        categoria = override_inv or default_planta or "plantas_ornamentales"
        markup = float(markups_b2c.get(categoria, 0.20))
        precio_mayorista = float(inv.get("precio_mayorista") or 0)
        precio_comprador = round(precio_mayorista * (1 + markup))
        subtotal = precio_comprador * req_item.cantidad

        planta = inv.get("plantas") or {}
        vivero = inv.get("viveros") or {}

        items_validados.append({
            "inventario_id": req_item.inventario_id,
            "cantidad": req_item.cantidad,
            "nombre": planta.get("nombre_comun", "Planta"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "municipio": vivero.get("ciudad"),
            "foto_ia_url": inv.get("foto_ia_url"),
            "precio_comprador_unitario": precio_comprador,
            "subtotal": subtotal,
            "categoria": categoria,
            "logistics_tier": tier,
        })
        total_plantas += req_item.cantidad
        total_cop += subtotal
        tiers_presentes.append(tier)

    # Si hay errores, devolver 400 con detalles
    if errores:
        return {
            "ok": False,
            "errores": errores,
            "items_validados": items_validados,
            "total_plantas": total_plantas,
            "total_cop": total_cop,
        }

    # Detectar si hay materas (afecta días de entrega)
    tiene_materas = any(
        (it.get("categoria") == "materas") for it in items_validados
    )
    dias_entrega_minimos = 10 if tiene_materas else 5

    return {
        "ok": True,
        "items": items_validados,
        "total_plantas": total_plantas,
        "total_cop": total_cop,
        "tier_logistico": _tier_mayor(tiers_presentes),
        "tiene_materas": tiene_materas,
        "dias_entrega_minimos": dias_entrega_minimos,
    }


# ═══════════════════════════════════════════════════════════
# Endpoint 2: CALCULAR FLETE
# ═══════════════════════════════════════════════════════════

@router.post("/calcular-flete")
async def calcular_flete(req: CalcularFleteRequest):
    """Calcula flete para el pedido guest según ciudad + tier logístico.

    Toma el tier MAYOR entre todos los items (el camión debe caber lo más grande).
    Usa tabla tarifas_logistica si existe, fallback stub por zona.
    """
    db = admin()
    inv_ids = [it.inventario_id for it in req.items]

    # Traer tiers de todos los items
    tier_resp = db.table("inventario").select(
        "inventario_id, logistics_tier"
    ).in_("inventario_id", inv_ids).execute()
    tiers = [r.get("logistics_tier", "M") for r in (tier_resp.data or [])]

    if not tiers:
        raise HTTPException(400, detail="No se encontraron productos válidos")

    tier_mayor = _tier_mayor(tiers)

    # Guardrail: guest no debería tener L/XL (validate-cart lo bloquea antes)
    if tier_mayor not in ("S", "M"):
        raise HTTPException(400, detail="Este pedido requiere cuenta empresa")

    ciudad = req.ciudad.strip()
    if not ciudad:
        raise HTTPException(400, detail="Ciudad requerida")

    flete_cop = _calcular_flete_por_tarifa(db, ciudad, tier_mayor)
    zona = _resolver_zona(ciudad)

    return {
        "ok": True,
        "ciudad": ciudad,
        "tier": tier_mayor,
        "zona": zona,
        "flete_cop": flete_cop,
        "dias_entrega_estimados": 5 if zona == 1 else 7,
    }
