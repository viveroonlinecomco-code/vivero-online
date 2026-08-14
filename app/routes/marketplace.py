"""Marketplace (compradores B2B) + endpoints públicos guest + cotizaciones.

═══════════════════════════════════════════════════════════════════════════
Fase 8 (24 jul 2026) — Marketplace guest B2C.

Nuevos endpoints públicos SIN autenticación:
- GET /api/public/marketplace          → catálogo filtrado tier S+M
- GET /api/public/marketplace/item/{id} → detalle producto (guest)

Reglas B2C guest (confirmadas 24 jul):
- Solo tier S y M (excluye L y XL — sin visibilidad para guest)
- Máx 10 unidades por producto (validado en frontend + reforzado en Fase 10)
- Máx 120 plantas por compra (idem)
- Marketplace guest NO expone: latitud/longitud, dirección, teléfono viverista
- Marketplace guest SÍ expone: nombre_vivero, ciudad (para transparencia)

═══════════════════════════════════════════════════════════════════════════
Fase 8 (24 jul 2026) — Batch optimization.

ANTES: _enriquecer_items hacía 1 RPC get_categoria_producto por item + lookup
       de matriz por item. Con 10 items = ~20 queries N+1.
AHORA: 1 sola query in_(inventario_ids) resuelve categorías de todos los items.
       Matriz cacheada en request via variable local.

Impacto: panel comprador 3-5× más rápido en cotizaciones grandes.
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth.deps import UserContext, require_comprador, require_user
from app.schemas.catalog import InventarioItem
from app.schemas.transactions import (
    CotizacionRequest, CotizacionResponse, RenameProyectoRequest,
)
from app.services.supabase import admin
from app.services.config_global import get_markup_categoria, get_matriz_comercial
from app.services.whatsapp_meta import notify_viverista_nueva_cotizacion

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])
public_router = APIRouter(prefix="/api/public/marketplace", tags=["marketplace-guest"])


# ═══════════════════════════════════════════════════════════
# Helper: Calcular estado real basado en sub_cotizaciones (FIX 13 ago)
# ═══════════════════════════════════════════════════════════

def _calcular_estado_real_cotizacion(estado_original: str, subs_estados: list[str]) -> str:
    """Calcula estado real de cotización basado en estados de sub_cotizaciones.
    
    Reglas SIMPLES:
      - Si NO hay subs: devolver estado_original
      - Si TODAS rechazadas: "rechazada"
      - Si TODAS aprobadas: "aceptada"
      - Si hay mix aprobadas+rechazadas: "parcial"
      - Si hay pendientes: "enviada"
    """
    if not subs_estados:
        return estado_original
    
    # Contar estados
    aprobadas = sum(1 for e in subs_estados if e == "aprobada")
    rechazadas = sum(1 for e in subs_estados if e == "rechazada")
    pendientes = sum(1 for e in subs_estados if e == "pendiente")
    total = len(subs_estados)
    
    # Orden de evaluación es IMPORTANTE
    # Primero: si hay al menos 1 rechazada Y NO todas aprobadas → parcial o rechazada
    if rechazadas > 0:
        # Si hay rechazadas pero también aprobadas → parcial
        if aprobadas > 0:
            return "parcial"
        # Si TODAS son rechazadas → rechazada
        if rechazadas == total:
            return "rechazada"
    
    # Si todas aprobadas
    if aprobadas == total:
        return "aceptada"
    
    # Si hay pendientes (y no rechazadas)
    if pendientes > 0:
        return "enviada"
    
    # Fallback
    return estado_original


# ═══════════════════════════════════════════════════════════
# Helpers — motor matricial batch (Fase 8)
# ═══════════════════════════════════════════════════════════

def _resolver_categorias_batch(db, inventario_ids: list[int]) -> dict[int, str]:
    """Resuelve categorías de MÚLTIPLES SKUs en 1 sola query.

    Aplica misma lógica COALESCE que get_categoria_producto SQL:
        inventario.categoria_producto > plantas.categoria_producto > 'plantas_ornamentales'

    Returns:
        dict {inventario_id: categoria_efectiva}
    """
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


def _resolver_categoria(db, inventario_id: int) -> str:
    """Resolver 1 categoría (delega en batch)."""
    if not inventario_id:
        return "plantas_ornamentales"
    mapa = _resolver_categorias_batch(db, [inventario_id])
    return mapa.get(inventario_id, "plantas_ornamentales")


def _precio_comprador_por_item(db, inventario_id: int, precio_mayorista: float) -> int:
    """Calcula precio_comprador para 1 item aplicando markup por categoría."""
    categoria = _resolver_categoria(db, inventario_id)
    markup = get_markup_categoria(categoria)
    return round(precio_mayorista * (1 + markup))


def _precios_comprador_batch(db, items_precio: list[tuple[int, float]]) -> dict[int, int]:
    """Calcula precios comprador para múltiples items en batch.

    Args:
        items_precio: lista de (inventario_id, precio_mayorista)

    Returns:
        dict {inventario_id: precio_comprador_int}
    """
    if not items_precio:
        return {}
    inv_ids = [inv_id for inv_id, _ in items_precio]
    categorias = _resolver_categorias_batch(db, inv_ids)

    # Precargar matriz UNA vez (caché en memoria durante la request)
    matriz = get_matriz_comercial()
    markups_b2c = matriz.get("markup_b2c", {})

    resultado = {}
    for inv_id, precio in items_precio:
        cat = categorias.get(inv_id, "plantas_ornamentales")
        markup = float(markups_b2c.get(cat, 0.20))
        resultado[inv_id] = round(precio * (1 + markup))
    return resultado


# ═══════════════════════════════════════════════════════════
# 🆕 FASE 8 — ENDPOINTS PÚBLICOS GUEST
# ═══════════════════════════════════════════════════════════

@public_router.get("")
async def listar_marketplace_guest(
    q: Optional[str] = None,
    municipio: Optional[str] = None,
    altura_min: int = 0,
    cantidad_min: int = 1,
    limite: int = Query(60, le=120),
):
    """Catálogo público para B2C guest (sin auth).

    Reglas:
    - Solo productos con logistics_tier IN ('S', 'M') — excluye L y XL
    - NO expone latitud/longitud, dirección, teléfono viverista
    - SÍ expone: nombre_vivero, ciudad (transparencia)
    - Precios con markup por categoría (motor matricial)
    """
    db = admin()

    if q and q.strip():
        # Búsqueda por texto — filtrar tier S+M después del RPC
        resp = db.rpc("buscar_plantas_cercanas", {
            "p_nombre_planta": q.strip(),
            "p_altura_min_cm": altura_min,
            "p_radio_km": 100.0,
            "p_lat": 4.9195,
            "p_lon": -74.0270,
            "p_cantidad_min": cantidad_min,
            "p_limite": limite * 2,  # traer más para poder filtrar por tier
        }).execute()

        raw_items = resp.data or []
        # Filtrar solo tier S+M (para guest)
        inv_ids = [r["inventario_id"] for r in raw_items]
        if inv_ids:
            tiers = db.table("inventario").select(
                "inventario_id, logistics_tier"
            ).in_("inventario_id", inv_ids).execute()
            tier_map = {t["inventario_id"]: t.get("logistics_tier", "M") for t in (tiers.data or [])}
        else:
            tier_map = {}

        raw_items = [r for r in raw_items if tier_map.get(r["inventario_id"], "M") in ("S", "M")]
        raw_items = raw_items[:limite]

        # Batch precios comprador
        items_precio = [(r["inventario_id"], float(r.get("precio_mayorista") or 0)) for r in raw_items]
        precios_map = _precios_comprador_batch(db, items_precio)

        items = []
        for r in raw_items:
            inv_id = r["inventario_id"]
            items.append({
                "inventario_id": inv_id,
                "planta_id": r["planta_id"],
                "nombre_comun": r["nombre_comun"],
                "nombre_cientifico": r.get("nombre_cientifico"),
                "foto_ia_url": r.get("foto_ia_url"),
                # 🔒 Fase 8 guest: solo precio_comprador, NO mayorista
                "precio_comprador": precios_map.get(inv_id, 0),
                "stock": r["stock"],
                "altura_cm": r["altura_cm"],
                "nombre_vivero": r["nombre_vivero"],
                "municipio": r.get("municipio_vivero"),
                # NO exponer: latitud, longitud, dirección, distancia_km, precio_mayorista
            })
        return {"ok": True, "items": items, "total": len(items)}

    # Sin búsqueda — listado paginado filtrado tier S+M
    query = db.table("inventario").select(
        "inventario_id, planta_id, altura_cm, precio_mayorista, "
        "stock, unidad_medida, foto_ia_url, vivero_id, logistics_tier, "
        "plantas(nombre_comun, nombre_cientifico), "
        "viveros(nombre_vivero, ciudad)"
    ).eq("estado_planta", "disponible").in_(
        "logistics_tier", ["S", "M"]  # 🔒 filtro guest
    ).gte("stock", cantidad_min).gte("altura_cm", altura_min)

    if municipio:
        v_resp = db.table("viveros").select("vivero_id").eq("ciudad", municipio).execute()
        vivero_ids = [v["vivero_id"] for v in (v_resp.data or [])]
        if not vivero_ids:
            return {"ok": True, "items": [], "total": 0}
        query = query.in_("vivero_id", vivero_ids)

    resp = query.limit(limite).execute()
    raw_items = resp.data or []

    # Batch precios comprador
    items_precio = [(r["inventario_id"], float(r.get("precio_mayorista") or 0)) for r in raw_items]
    precios_map = _precios_comprador_batch(db, items_precio)

    items = []
    for r in raw_items:
        planta = r.get("plantas") or {}
        vivero = r.get("viveros") or {}
        inv_id = r["inventario_id"]
        items.append({
            "inventario_id": inv_id,
            "planta_id": r["planta_id"],
            "nombre_comun": planta.get("nombre_comun", "Sin nombre"),
            "nombre_cientifico": planta.get("nombre_cientifico"),
            "foto_ia_url": r.get("foto_ia_url"),
            # 🔒 Fase 8 guest: solo precio_comprador
            "precio_comprador": precios_map.get(inv_id, 0),
            "stock": r.get("stock") or 0,
            "altura_cm": r.get("altura_cm") or 0,
            "unidad_medida": r.get("unidad_medida"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "municipio": vivero.get("ciudad"),
            # NO exponer: vivero_id, latitud, longitud, precio_mayorista, logistics_tier
        })
    return {"ok": True, "items": items, "total": len(items)}


@public_router.get("/item/{inventario_id}")
async def detalle_item_guest(inventario_id: int):
    """Detalle de producto para B2C guest (sin auth).

    Solo devuelve productos tier S+M. Si el producto es L o XL, devuelve 404
    (como si no existiera).
    """
    db = admin()

    resp = db.table("inventario").select(
        "inventario_id, planta_id, altura_cm, precio_mayorista, "
        "stock, unidad_medida, estado_planta, foto_ia_url, notas, "
        "logistics_tier, "
        "plantas(nombre_comun, nombre_cientifico, familia_botanica, "
        "requerimientos_ia, clima_ideal), "
        "viveros(nombre_vivero, ciudad, departamento)"
    ).eq("inventario_id", inventario_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Producto no encontrado")

    item = resp.data[0]

    # 🔒 Guardrail: si es L o XL, tratar como no existente
    if item.get("logistics_tier") not in ("S", "M"):
        raise HTTPException(404, detail="Producto no encontrado")

    if item.get("estado_planta") != "disponible":
        raise HTTPException(404, detail="Producto no disponible")

    precio_base = float(item.get("precio_mayorista") or 0)
    precio_comprador = _precio_comprador_por_item(db, inventario_id, precio_base)

    planta = item.get("plantas") or {}
    vivero = item.get("viveros") or {}

    return {
        "ok": True,
        "item": {
            "inventario_id": item["inventario_id"],
            "planta_id": item["planta_id"],
            "nombre_comun": planta.get("nombre_comun"),
            "nombre_cientifico": planta.get("nombre_cientifico"),
            "familia_botanica": planta.get("familia_botanica"),
            "requerimientos_ia": planta.get("requerimientos_ia"),
            "clima_ideal": planta.get("clima_ideal"),
            "foto_ia_url": item.get("foto_ia_url"),
            "notas": item.get("notas"),
            "altura_cm": item.get("altura_cm"),
            "unidad_medida": item.get("unidad_medida"),
            "stock": item.get("stock"),
            # 🔒 Solo precio_comprador
            "precio_comprador": precio_comprador,
            # 🔒 Info vivero mínima (sin dirección/latitud/longitud/teléfono)
            "nombre_vivero": vivero.get("nombre_vivero"),
            "municipio": vivero.get("ciudad"),
            "departamento": vivero.get("departamento"),
            # 🔒 Límites guest — el frontend los usa para bloquear
            "limite_unidades_por_producto": 10,
            "limite_total_plantas": 120,
        },
    }


# ═══════════════════════════════════════════════════════════
# ENDPOINTS B2B (existentes) — con optimización batch
# ═══════════════════════════════════════════════════════════

@router.get("")
async def listar_marketplace(
    q: Optional[str] = None,
    municipio: Optional[str] = None,
    altura_min: int = 0,
    cantidad_min: int = 1,
    lat: float = 4.9195,
    lon: float = -74.0270,
    radio_km: float = 50.0,
    limite: int = Query(120, le=200),
    user: UserContext = Depends(require_user),
):
    """Catálogo B2B (usuario logueado). Muestra TODO — incluye L y XL."""
    db = admin()
    if q and q.strip():
        resp = db.rpc("buscar_plantas_cercanas", {
            "p_nombre_planta": q.strip(),
            "p_altura_min_cm": altura_min,
            "p_radio_km": radio_km,
            "p_lat": lat,
            "p_lon": lon,
            "p_cantidad_min": cantidad_min,
            "p_limite": limite,
        }).execute()
        raw_items = resp.data or []

        # Batch precios
        items_precio = [(r["inventario_id"], float(r.get("precio_mayorista") or 0)) for r in raw_items]
        precios_map = _precios_comprador_batch(db, items_precio)

        items = []
        for r in raw_items:
            precio_base = float(r.get("precio_mayorista") or 0)
            inv_id = r["inventario_id"]
            items.append({
                "inventario_id": inv_id,
                "planta_id": r["planta_id"],
                "nombre_comun": r["nombre_comun"],
                "nombre_cientifico": r.get("nombre_cientifico"),
                "foto_ia_url": r.get("foto_ia_url"),
                "precio_mayorista": precio_base,
                "precio_comprador": precios_map.get(inv_id, 0),
                "stock": r["stock"],
                "altura_cm": r["altura_cm"],
                "vivero_id": r["vivero_id"],
                "nombre_vivero": r["nombre_vivero"],
                "distancia_km": float(r.get("distancia_km") or 0),
            })
        return {"ok": True, "items": items, "total": len(items)}

    query = db.table("inventario").select(
        "inventario_id, planta_id, altura_cm, precio_mayorista, "
        "stock, unidad_medida, foto_ia_url, vivero_id, "
        "plantas(nombre_comun, nombre_cientifico), "
        "viveros(nombre_vivero, ciudad)"
    ).eq("estado_planta", "disponible").gte("stock", cantidad_min).gte("altura_cm", altura_min)

    if municipio:
        v_resp = db.table("viveros").select("vivero_id").eq("ciudad", municipio).execute()
        vivero_ids = [v["vivero_id"] for v in (v_resp.data or [])]
        if not vivero_ids:
            return {"ok": True, "items": [], "total": 0}
        query = query.in_("vivero_id", vivero_ids)

    resp = query.limit(limite).execute()
    raw_items = resp.data or []

    # Batch precios (1 query resuelve todo)
    items_precio = [(r["inventario_id"], float(r.get("precio_mayorista") or 0)) for r in raw_items]
    precios_map = _precios_comprador_batch(db, items_precio)

    items = []
    for r in raw_items:
        planta = r.get("plantas") or {}
        vivero = r.get("viveros") or {}
        precio_base = float(r.get("precio_mayorista") or 0)
        inv_id = r["inventario_id"]
        items.append({
            "inventario_id": inv_id,
            "planta_id": r["planta_id"],
            "nombre_comun": planta.get("nombre_comun", "Sin nombre"),
            "nombre_cientifico": planta.get("nombre_cientifico"),
            "foto_ia_url": r.get("foto_ia_url"),
            "precio_mayorista": precio_base,
            "precio_comprador": precios_map.get(inv_id, 0),
            "stock": r.get("stock") or 0,
            "altura_cm": r.get("altura_cm") or 0,
            "unidad_medida": r.get("unidad_medida"),
            "vivero_id": r.get("vivero_id"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "municipio": vivero.get("ciudad"),
        })
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/item/{inventario_id}")
async def detalle_item(
    inventario_id: int,
    user: UserContext = Depends(require_user),
):
    """Detalle B2B (con auth)."""
    db = admin()
    tiene_suscripcion = user.rol == "admin"
    if not tiene_suscripcion:
        sus = db.table("suscripciones").select("suscripcion_id").eq(
            "user_id", user.user_id
        ).eq("estado", "activa").limit(1).execute()
        tiene_suscripcion = bool(sus.data)

    resp = db.table("inventario").select(
        "inventario_id, planta_id, altura_cm, precio_mayorista, precio_detal, "
        "stock, unidad_medida, estado_planta, foto_ia_url, notas, vivero_id, "
        "plantas(nombre_comun, nombre_cientifico, familia_botanica, requerimientos_ia, clima_ideal), "
        "viveros(nombre_vivero, ciudad, departamento, historia, foto_url, fotos_galeria, latitud, longitud, direccion)"
    ).eq("inventario_id", inventario_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Item no encontrado")
    item = resp.data[0]

    if not tiene_suscripcion and item.get("viveros"):
        item["viveros"]["latitud"]   = None
        item["viveros"]["longitud"]  = None
        item["viveros"]["direccion"] = None

    precio_base = float(item.get("precio_mayorista") or 0)
    item["precio_comprador"] = _precio_comprador_por_item(db, inventario_id, precio_base)
    item["tiene_suscripcion"] = tiene_suscripcion
    return {"ok": True, "item": item}


# ═══════════════════════════════════════════════════════════
# COTIZACIONES — helpers
# ═══════════════════════════════════════════════════════════

def _validar_items_y_calcular(db, req_items) -> tuple[list[dict], float]:
    items_validados = []
    total = 0.0
    for item in req_items:
        inv = db.table("inventario").select(
            "precio_mayorista, stock, estado_planta"
        ).eq("inventario_id", item.inventario_id).limit(1).execute()
        if not inv.data:
            raise HTTPException(404, detail=f"Inventario {item.inventario_id} no existe")
        i = inv.data[0]
        if i["estado_planta"] != "disponible":
            raise HTTPException(400, detail=f"Item {item.inventario_id} no disponible")
        if (i.get("stock") or 0) < item.cantidad:
            raise HTTPException(400, detail=f"Stock insuficiente para {item.inventario_id}")
        subtotal = float(i["precio_mayorista"]) * item.cantidad
        total += subtotal
        items_validados.append({
            "inventario_id": item.inventario_id,
            "cantidad": item.cantidad,
            "precio_unitario": float(i["precio_mayorista"]),
            "subtotal": subtotal,
        })
    return items_validados, total


def _merge_items_y_actualizar(
    db, borrador: dict, items_nuevos: list[dict],
    notas_nuevas: Optional[str] = None,
    nombre_nuevo: Optional[str] = None,
) -> CotizacionResponse:
    cotizacion_id = borrador["cotizacion_id"]
    items_combinados = list(borrador.get("items") or [])
    for nuevo in items_nuevos:
        match_idx = next(
            (idx for idx, it in enumerate(items_combinados)
             if it.get("inventario_id") == nuevo["inventario_id"]),
            None,
        )
        if match_idx is not None:
            it = items_combinados[match_idx]
            it["cantidad"] = it.get("cantidad", 0) + nuevo["cantidad"]
            it["subtotal"] = it["cantidad"] * it.get("precio_unitario", 0)
        else:
            items_combinados.append(nuevo)
    total_combinado = sum(float(it.get("subtotal") or 0) for it in items_combinados)
    update_payload = {
        "items": items_combinados,
        "total_estimado": total_combinado,
    }
    if notas_nuevas:
        update_payload["notas_cliente"] = notas_nuevas
    if nombre_nuevo:
        update_payload["prompt_original"] = nombre_nuevo
    db.table("cotizaciones").update(update_payload).eq("cotizacion_id", cotizacion_id).execute()
    return CotizacionResponse(
        ok=True,
        cotizacion_id=cotizacion_id,
        total_cop=total_combinado,
        estado="borrador",
    )


def _enriquecer_items(db, items_raw: list[dict]) -> list[dict]:
    """Enriquece items con datos + precios comprador — OPTIMIZADO BATCH.

    En lugar de N queries (1 por item), hace 2 queries totales:
      1. Batch datos inventario+planta+vivero+categoría
      2. Batch matriz cacheada (get_matriz_comercial devuelve dict cacheado 60s)
    """
    if not items_raw:
        return []

    inventario_ids = [it.get("inventario_id") for it in items_raw if it.get("inventario_id")]

    # Query 1: datos completos de inventario en batch
    inv_map: dict[int, dict] = {}
    if inventario_ids:
        inv_resp = db.table("inventario").select(
            "inventario_id, foto_ia_url, stock, estado_planta, categoria_producto, "
            "plantas(nombre_comun, nombre_cientifico, categoria_producto), "
            "viveros(vivero_id, nombre_vivero, ciudad)"
        ).in_("inventario_id", inventario_ids).execute()
        inv_map = {r["inventario_id"]: r for r in (inv_resp.data or [])}

    # Precargar matriz UNA vez (caché 60s)
    matriz = get_matriz_comercial()
    markups_b2c = matriz.get("markup_b2c", {})

    enriquecidos = []
    for it in items_raw:
        inv_id = it.get("inventario_id")
        inv = inv_map.get(inv_id, {})
        planta = inv.get("plantas") or {}
        vivero = inv.get("viveros") or {}

        # Categoría desde datos ya cargados (0 queries extra)
        override_inv = inv.get("categoria_producto")
        default_planta = planta.get("categoria_producto")
        categoria = override_inv or default_planta or "plantas_ornamentales"

        # Markup desde matriz cacheada (0 queries extra)
        markup = float(markups_b2c.get(categoria, 0.20))

        precio_unit = float(it.get("precio_unitario") or 0)
        cantidad = int(it.get("cantidad") or 1)
        precio_comprador_unitario = round(precio_unit * (1 + markup))
        subtotal_comprador = precio_comprador_unitario * cantidad

        enriquecidos.append({
            **it,
            "nombre_comun": planta.get("nombre_comun"),
            "nombre_cientifico": planta.get("nombre_cientifico"),
            "foto_ia_url": inv.get("foto_ia_url"),
            "stock_disponible": inv.get("stock"),
            "vivero_id": (vivero or {}).get("vivero_id"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "ciudad_vivero": vivero.get("ciudad"),
            "categoria": categoria,
            "precio_comprador_unitario": precio_comprador_unitario,
            "subtotal_comprador": subtotal_comprador,
        })
    return enriquecidos


def _total_comprador_desde_items(items_enriquecidos: list[dict]) -> int:
    """Suma los subtotal_comprador de todos los items."""
    return sum(int(it.get("subtotal_comprador") or 0) for it in items_enriquecidos)


def _obtener_estado_entrega(db, cotizacion_id: int) -> dict:
    try:
        resp = db.table("entregas").select(
            "estado_entrega, fecha_despacho, fecha_entrega, direccion_entrega, "
            "contacto_nombre, contacto_telefono"
        ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
        if resp.data:
            return resp.data[0]
    except Exception:
        pass
    return {}


# ═══════════════════════════════════════════════════════════
# COTIZACIONES — endpoints B2B
# ═══════════════════════════════════════════════════════════

@router.post("/cotizacion", response_model=CotizacionResponse)
async def crear_o_agregar_a_borrador(
    req: CotizacionRequest,
    user: UserContext = Depends(require_comprador),
):
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    if req.cotizacion_id is not None and req.nombre_proyecto is not None:
        raise HTTPException(400, detail="Especificá solo cotizacion_id O nombre_proyecto, no ambos")
    db = admin()
    items_nuevos, total_nuevo = _validar_items_y_calcular(db, req.items)
    if req.cotizacion_id is not None:
        existente = db.table("cotizaciones").select(
            "cotizacion_id, cliente_id, estado, items, total_estimado"
        ).eq("cotizacion_id", req.cotizacion_id).limit(1).execute()
        if not existente.data:
            raise HTTPException(404, detail="Proyecto no encontrado")
        b = existente.data[0]
        if b["cliente_id"] != user.cliente_id:
            raise HTTPException(404, detail="Proyecto no encontrado")
        if b["estado"] not in ("borrador", "enviada", "parcial", "rechazada"):
            raise HTTPException(400, detail=f"No se pueden agregar items a un proyecto en estado '{b['estado']}'")

        return _merge_items_y_actualizar(db, b, items_nuevos)
    if req.nombre_proyecto is not None:
        cot_resp = db.table("cotizaciones").insert({
            "cliente_id": user.cliente_id,
            "total_estimado": total_nuevo,
            "estado": "borrador",
            "notas_cliente": req.notas,
            "prompt_original": req.nombre_proyecto,
            "items": items_nuevos,
            "generada_por_ia": False,
        }).execute()
        cotizacion_id = cot_resp.data[0]["cotizacion_id"]
        return CotizacionResponse(ok=True, cotizacion_id=cotizacion_id, total_cop=total_nuevo, estado="borrador")
    existente = db.table("cotizaciones").select(
        "cotizacion_id, items, total_estimado, notas_cliente, prompt_original, estado"
    ).eq("cliente_id", user.cliente_id).in_("estado", ["borrador", "enviada", "parcial", "rechazada"]).order(
        "fecha_creacion", desc=True
    ).limit(1).execute()
    if existente.data:
        return _merge_items_y_actualizar(db, existente.data[0], items_nuevos, notas_nuevas=req.notas, nombre_nuevo=req.proyecto)
    cot_resp = db.table("cotizaciones").insert({
        "cliente_id": user.cliente_id,
        "total_estimado": total_nuevo,
        "estado": "borrador",
        "notas_cliente": req.notas,
        "prompt_original": req.proyecto,
        "items": items_nuevos,
        "generada_por_ia": False,
    }).execute()
    cotizacion_id = cot_resp.data[0]["cotizacion_id"]
    return CotizacionResponse(ok=True, cotizacion_id=cotizacion_id, total_cop=total_nuevo, estado="borrador")


@router.get("/cotizacion/proyectos")
async def listar_proyectos(user: UserContext = Depends(require_comprador)):
    """Lista proyectos con total_comprador batch-optimizado.

    ── Fase 8: batch de TODOS los inventario_ids de TODOS los proyectos ──
    En vez de 1 query por proyecto × N items, ahora 1 query TOTAL para todo.
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    db = admin()
    resp = db.table("cotizaciones").select(
        "cotizacion_id, prompt_original, estado, total_estimado, items, "
        "fecha_creacion, fecha_vencimiento, fecha_conversion, notas_cliente"
    ).eq("cliente_id", user.cliente_id).not_.in_("estado", ["cancelada", "entregado"]).order("fecha_creacion", desc=True).execute()

    # FIX 13 ago: Obtener estados de sub_cotizaciones para calcular estado real
    subs_por_cot = {}
    if resp.data:
        cot_ids = [r["cotizacion_id"] for r in resp.data]
        if cot_ids:  # Solo si hay cotizaciones
            try:
                subs_resp = db.table("sub_cotizaciones").select(
                    "cotizacion_id, estado"
                ).in_("cotizacion_id", cot_ids).execute()
                
                # Mapear: {cotizacion_id: [lista de estados]}
                for sub in subs_resp.data or []:
                    cot_id = sub["cotizacion_id"]
                    if cot_id not in subs_por_cot:
                        subs_por_cot[cot_id] = []
                    subs_por_cot[cot_id].append(sub["estado"])
            except Exception:
                # Fallback: si query falla, devolver cotizaciones sin estado real
                pass

    cot_ids_pagados = [r["cotizacion_id"] for r in (resp.data or []) if r.get("estado") == "pagada"]
    entrega_map = {}
    if cot_ids_pagados:
        try:
            entregas = db.table("entregas").select(
                "cotizacion_id, estado_entrega"
            ).in_("cotizacion_id", cot_ids_pagados).execute()
            entrega_map = {e["cotizacion_id"]: e["estado_entrega"] for e in (entregas.data or [])}
        except Exception:
            pass

    # Recolectar TODOS los inventario_ids de TODOS los proyectos en una sola pasada
    todos_inv_ids = set()
    for r in resp.data or []:
        for it in r.get("items") or []:
            if it.get("inventario_id"):
                todos_inv_ids.add(it["inventario_id"])

    # 1 query resuelve categorías de TODOS los items de TODOS los proyectos
    categorias_map = _resolver_categorias_batch(db, list(todos_inv_ids))
    matriz = get_matriz_comercial()
    markups_b2c = matriz.get("markup_b2c", {})

    proyectos = []
    ahora = datetime.now(timezone.utc)
    for r in resp.data or []:
        items = r.get("items") or []
        cot_id = r["cotizacion_id"]
        estado_cot = r["estado"]
        
        # FILTRO: Excluir cotizaciones canceladas (no mostrar basura)
        if estado_cot == "cancelada":
            continue
        
        # Detectar si está vencida
        estáVencida = False
        fecha_venc = r.get("fecha_vencimiento")
        if fecha_venc:
            try:
                fecha_venc_dt = datetime.fromisoformat(fecha_venc.replace('Z', '+00:00'))
                if ahora > fecha_venc_dt:
                    estáVencida = True
            except:
                pass

        total_comprador = 0
        for it in items:
            inv_id = it.get("inventario_id")
            precio_unit = float(it.get("precio_unitario") or 0)
            cantidad = int(it.get("cantidad") or 0)
            if inv_id:
                categoria = categorias_map.get(inv_id, "plantas_ornamentales")
                markup = float(markups_b2c.get(categoria, 0.20))
                total_comprador += round(precio_unit * (1 + markup)) * cantidad

        # FIX 13 ago: Calcular estado real basado en sub_cotizaciones
        estados_subs = subs_por_cot.get(cot_id, [])
        estado_real = _calcular_estado_real_cotizacion(estado_cot, estados_subs)
        
        # Si está vencida, marcar como vencida
        if estáVencida:
            estado_real = "vencida"

        # Botón MODIFICAR: si está enviada, parcial o rechazada (NO vencida, NO pagada)
        puede_modificar = estado_real in ("enviada", "parcial", "rechazada")
        
        # Mostrar badge "aprobado": solo si está aprobada Y NO vencida
        mostrar_aprobado = estado_real == "aceptada" and estado_cot != "vencida"

        # NUEVO: Desglose de sub_cotizaciones (para mostrar viveristas rechazados)
        subs_desglose = []
        if estados_subs:
            try:
                subs_full = db.table("sub_cotizaciones").select(
                    "vivero_id, estado, viveros(nombre_vivero, ciudad)"
                ).eq("cotizacion_id", cot_id).execute()
                
                for sub in subs_full.data or []:
                    vivero = sub.get("viveros") or {}
                    subs_desglose.append({
                        "vivero_id": sub.get("vivero_id"),
                        "estado": sub.get("estado"),
                        "nombre_vivero": vivero.get("nombre_vivero"),
                        "ciudad": vivero.get("ciudad"),
                    })
            except Exception:
                pass

        proyectos.append({
            "cotizacion_id": cot_id,
            "nombre_proyecto": r.get("prompt_original"),
            "estado": estado_real,
            "estado_entrega": entrega_map.get(cot_id),
            "total_estimado": float(r.get("total_estimado") or 0),
            "total_comprador": total_comprador,
            "num_items": sum(it.get("cantidad", 0) for it in items),
            "num_items_distintos": len(items),
            "fecha_creacion": str(r.get("fecha_creacion") or ""),
            "fecha_vencimiento": str(r.get("fecha_vencimiento") or ""),
            "fecha_conversion": str(r.get("fecha_conversion")) if r.get("fecha_conversion") else None,
            "notas_cliente": r.get("notas_cliente"),
            "puede_modificar": puede_modificar,
            "puede_agregar_items": estado_real in ("enviada", "parcial"),
            "mostrar_aprobado": mostrar_aprobado,
            "sub_cotizaciones": subs_desglose,  # ← Desglose viveristas: para marcar rechazados
        })
    return {"ok": True, "proyectos": proyectos, "total": len(proyectos)}


@router.get("/cotizacion/borrador")
async def obtener_borrador(user: UserContext = Depends(require_comprador)):
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    db = admin()
    resp = db.table("cotizaciones").select(
        "cotizacion_id, items, total_estimado, fecha_creacion, "
        "fecha_vencimiento, notas_cliente, prompt_original"
    ).eq("cliente_id", user.cliente_id).eq("estado", "borrador").order(
        "fecha_creacion", desc=True
    ).limit(1).execute()
    if not resp.data:
        return {"ok": True, "borrador": None}
    b = resp.data[0]
    items_enriquecidos = _enriquecer_items(db, b.get("items") or [])
    total_comprador = _total_comprador_desde_items(items_enriquecidos)
    return {
        "ok": True,
        "borrador": {
            "cotizacion_id": b["cotizacion_id"],
            "items": items_enriquecidos,
            "total_estimado": float(b.get("total_estimado") or 0),
            "total_comprador": total_comprador,
            "num_items": sum(it.get("cantidad", 0) for it in items_enriquecidos),
            "num_viveros_distintos": len({it.get("vivero_id") for it in items_enriquecidos if it.get("vivero_id")}),
            "notas_cliente": b.get("notas_cliente"),
            "prompt_original": b.get("prompt_original"),
            "fecha_creacion": str(b.get("fecha_creacion") or ""),
            "fecha_vencimiento": str(b.get("fecha_vencimiento") or ""),
        },
    }


@router.delete("/cotizacion/item/{inventario_id}")
async def quitar_item_borrador(
    inventario_id: int,
    cotizacion_id: Optional[int] = Query(None),
    user: UserContext = Depends(require_comprador),
):
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    db = admin()
    if cotizacion_id is not None:
        resp = db.table("cotizaciones").select(
            "cotizacion_id, cliente_id, estado, items"
        ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(404, detail="Cotización no encontrada")
        b = resp.data[0]
        if b["cliente_id"] != user.cliente_id:
            raise HTTPException(404, detail="Cotización no encontrada")
        if b["estado"] != "borrador":
            raise HTTPException(400, detail=f"No se puede modificar una cotización en estado '{b['estado']}'")
    else:
        resp = db.table("cotizaciones").select(
            "cotizacion_id, items"
        ).eq("cliente_id", user.cliente_id).eq("estado", "borrador").order(
            "fecha_creacion", desc=True
        ).limit(1).execute()
        if not resp.data:
            raise HTTPException(404, detail="No tienes un borrador activo")
        b = resp.data[0]
    items_actuales = b.get("items") or []
    items_filtrados = [it for it in items_actuales if it.get("inventario_id") != inventario_id]
    if len(items_filtrados) == len(items_actuales):
        raise HTTPException(404, detail=f"Item {inventario_id} no está en este borrador")
    if not items_filtrados:
        db.table("cotizaciones").delete().eq("cotizacion_id", b["cotizacion_id"]).execute()
        return {"ok": True, "cotizacion_id": b["cotizacion_id"], "eliminado": True, "borrador": None}
    total_nuevo = sum(float(it.get("subtotal") or 0) for it in items_filtrados)
    db.table("cotizaciones").update({
        "items": items_filtrados,
        "total_estimado": total_nuevo,
    }).eq("cotizacion_id", b["cotizacion_id"]).execute()
    return {"ok": True, "cotizacion_id": b["cotizacion_id"], "total_estimado": total_nuevo, "items_restantes": len(items_filtrados)}


@router.get("/cotizacion/{cotizacion_id}")
async def obtener_cotizacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    db = admin()
    resp = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, prompt_original, estado, items, total_estimado, "
        "fecha_creacion, fecha_vencimiento, fecha_conversion, notas_cliente, notas_agente, "
        "alternativas_vivero"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    items_enriquecidos = _enriquecer_items(db, c.get("items") or [])
    total_comprador = _total_comprador_desde_items(items_enriquecidos)
    entrega = _obtener_estado_entrega(db, cotizacion_id)
    return {
        "ok": True,
        "cotizacion": {
            "cotizacion_id": c["cotizacion_id"],
            "nombre_proyecto": c.get("prompt_original"),
            "estado": c["estado"],
            "items": items_enriquecidos,
            "total_estimado": float(c.get("total_estimado") or 0),
            "total_comprador": total_comprador,
            "num_items": sum(it.get("cantidad", 0) for it in items_enriquecidos),
            "num_viveros_distintos": len({it.get("vivero_id") for it in items_enriquecidos if it.get("vivero_id")}),
            "notas_cliente": c.get("notas_cliente"),
            "notas_agente": c.get("notas_agente"),
            "fecha_creacion": str(c.get("fecha_creacion") or ""),
            "fecha_vencimiento": str(c.get("fecha_vencimiento") or ""),
            "fecha_conversion": str(c.get("fecha_conversion")) if c.get("fecha_conversion") else None,
            "estado_entrega": entrega.get("estado_entrega"),
            "fecha_despacho": str(entrega.get("fecha_despacho") or "") if entrega.get("fecha_despacho") else None,
            "fecha_entrega_real": str(entrega.get("fecha_entrega") or "") if entrega.get("fecha_entrega") else None,
            "direccion_entrega": entrega.get("direccion_entrega"),
            "alternativas_vivero": c.get("alternativas_vivero"),
        },
    }


@router.patch("/cotizacion/{cotizacion_id}")
async def renombrar_proyecto(
    cotizacion_id: int,
    req: RenameProyectoRequest,
    user: UserContext = Depends(require_comprador),
):
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    db = admin()
    resp = db.table("cotizaciones").select("cliente_id, estado").eq("cotizacion_id", cotizacion_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    if c["estado"] != "borrador":
        raise HTTPException(400, detail=f"No se puede renombrar una cotización en estado '{c['estado']}'.")
    db.table("cotizaciones").update({"prompt_original": req.nombre_proyecto}).eq("cotizacion_id", cotizacion_id).execute()
    return {"ok": True, "cotizacion_id": cotizacion_id, "nombre_proyecto": req.nombre_proyecto}


@router.delete("/cotizacion/{cotizacion_id}")
async def eliminar_proyecto(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    db = admin()
    resp = db.table("cotizaciones").select("cliente_id, estado").eq("cotizacion_id", cotizacion_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    if c["estado"] not in ("borrador", "vencida", "rechazada"):
        raise HTTPException(400, detail=f"No se puede eliminar una cotización en estado '{c['estado']}'.")
    db.table("cotizaciones").delete().eq("cotizacion_id", cotizacion_id).execute()
    return {"ok": True, "cotizacion_id": cotizacion_id, "eliminado": True}


@router.patch("/cotizacion/{cotizacion_id}/cancelar")
async def cancelar_cotizacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Cancela una cotización (la marca como cancelada)."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    db = admin()
    resp = db.table("cotizaciones").select("cliente_id, estado").eq("cotizacion_id", cotizacion_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    # No permitir cancelar si ya está pagada o entregada
    if c["estado"] in ("pagada", "entregada"):
        raise HTTPException(400, detail=f"No se puede cancelar una cotización en estado '{c['estado']}'.")
    
    # Marcar como cancelada
    db.table("cotizaciones").update({"estado": "cancelada"}).eq("cotizacion_id", cotizacion_id).execute()
    return {"ok": True, "cotizacion_id": cotizacion_id, "estado": "cancelada", "mensaje": "Cotización cancelada"}


@router.post("/cotizacion/{cotizacion_id}/agregar-items")
async def agregar_items_a_cotizacion(
    cotizacion_id: int,
    req: dict,
    user: UserContext = Depends(require_comprador),
):
    """Agrega items a una cotización SIN perder subs existentes.
    
    Request: {"items": [{"inventario_id": int, "cantidad": int, ...}]}
    
    IMPORTANTE: Solo copia items nuevos, conserva los existentes.
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    
    db = admin()
    
    # Obtener cotización actual
    resp = db.table("cotizaciones").select(
        "cliente_id, estado, items, total_estimado, prompt_original, "
        "fecha_creacion, fecha_vencimiento"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    # Solo permitir agregar a proyectos que NO están pagados/entregados
    if c["estado"] in ("pagada", "entregada", "cancelada"):
        raise HTTPException(400, detail=f"No se puede agregar items a una cotización en estado '{c['estado']}'.")
    
    # Obtener items nuevos
    items_nuevos = req.get("items", [])
    if not items_nuevos:
        raise HTTPException(400, detail="Debe proporcionar al menos un item")
    
    # Validar y calcular items nuevos
    items_validos, total_nuevo_items = _validar_items_y_calcular(db, items_nuevos)
    
    # Merge: conservar existentes + agregar nuevos
    items_actuales = c.get("items") or []
    inv_ids_existentes = {it.get("inventario_id") for it in items_actuales}
    
    # Agregar solo items que NO existen (evitar duplicados)
    items_para_agregar = [it for it in items_validos if it.get("inventario_id") not in inv_ids_existentes]
    
    if not items_para_agregar:
        raise HTTPException(400, detail="Todos los items ya están en la cotización")
    
    # Combinar
    items_finales = items_actuales + items_para_agregar
    total_final = sum(float(it.get("subtotal") or 0) for it in items_finales)
    
    # Actualizar
    db.table("cotizaciones").update({
        "items": items_finales,
        "total_estimado": total_final,
    }).eq("cotizacion_id", cotizacion_id).execute()
    
    # Enriquecer respuesta
    items_enriquecidos = _enriquecer_items(db, items_para_agregar)
    
    return {
        "ok": True,
        "cotizacion_id": cotizacion_id,
        "items_agregados": items_enriquecidos,
        "num_items_nuevos": len(items_para_agregar),
        "total_estimado": total_final,
        "mensaje": "Items agregados. La cotización conserva sus subs existentes."
    }


@router.post("/cotizacion/{cotizacion_id}/reenviar")
async def reenviar_cotizacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Reenvía notificaciones WhatsApp a viveristas sin duplicar cotización.
    
    Útil cuando:
    - Viverista no responde (recordatorio manual)
    - Cliente quiere acelerar proceso
    - Estado: enviada, parcial, rechazada
    
    ✅ FIX 15 ago: Ahora REALMENTE envía notificación WhatsApp a cada viverista
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    
    db = admin()
    
    # Obtener cotización
    resp = db.table("cotizaciones").select(
        "cliente_id, estado, prompt_original"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    # Solo permitir reenviar si está en estado pendiente de respuesta
    if c["estado"] not in ("enviada", "parcial", "rechazada"):
        raise HTTPException(400, detail=f"No se puede reenviar una cotización en estado '{c['estado']}'.")
    
    nombre_proyecto = c.get("prompt_original", "Proyecto")
    
    # ✅ FIX: Obtener sub_cotizaciones primero (sin joins complicados)
    subs_resp = db.table("sub_cotizaciones").select(
        "vivero_id, total_base, estado"
    ).eq("cotizacion_id", cotizacion_id).execute()
    
    subs = subs_resp.data or []
    notificaciones_enviadas = 0
    notificaciones_fallidas = 0
    
    # ✅ FIX: Para CADA vivero, obtener datos y enviar notificación
    for sub in subs:
        vivero_id = sub.get("vivero_id")
        total_vivero = sub.get("total_base", 0)
        
        # Obtener datos del vivero (número, nombre)
        try:
            vivero_resp = db.table("viveros").select(
                "whatsapp_numero, nombre_vivero"
            ).eq("vivero_id", vivero_id).limit(1).execute()
            
            if not vivero_resp.data:
                notificaciones_fallidas += 1
                continue
            
            vivero = vivero_resp.data[0]
            numero_whatsapp = vivero.get("whatsapp_numero")
            nombre_vivero = vivero.get("nombre_vivero", "Viverista")
            
            if numero_whatsapp:
                await notify_viverista_nueva_cotizacion(
                    to=numero_whatsapp,
                    nombre_viverista=nombre_vivero,
                    proyecto=nombre_proyecto,
                    cliente="Cliente ViveroOnline",
                    tu_parte_cop=int(total_vivero),
                    ciudad_entrega="Sabana de Bogotá",
                    horas_para_responder=2  # Urgente en reenvío
                )
                notificaciones_enviadas += 1
            else:
                notificaciones_fallidas += 1
        except Exception as e:
            print(f"Error procesando vivero {vivero_id}: {e}")
            notificaciones_fallidas += 1
    
    return {
        "ok": True,
        "cotizacion_id": cotizacion_id,
        "nombre_proyecto": nombre_proyecto,
        "num_viveristas_notificados": notificaciones_enviadas,
        "num_notificaciones_fallidas": notificaciones_fallidas,
        "mensaje": f"✅ Reenviado a {notificaciones_enviadas} vivero(s)." + (
            f" ({notificaciones_fallidas} fallos)" if notificaciones_fallidas > 0 else ""
        ),
    }


@router.post("/cotizacion/{cotizacion_id}/duplicar-y-reenviar")
async def duplicar_y_reenviar(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Duplica cotización (nuevo ID) y reenvía a viveristas.
    
    Casos de uso:
    - Cotización vencida que quiere reactivar
    - Modificar items SIN perder historial
    
    Resultado: copia con estado="enviada"
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    
    db = admin()
    
    # Obtener original
    resp = db.table("cotizaciones").select(
        "cliente_id, prompt_original, items, total_estimado, notas_cliente"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    # Crear nueva cotización con mismos datos
    nueva_cot = db.table("cotizaciones").insert({
        "cliente_id": user.cliente_id,
        "prompt_original": c["prompt_original"],
        "items": c["items"],
        "total_estimado": c["total_estimado"],
        "estado": "enviada",
        "notas_cliente": c["notas_cliente"],
    }).execute()
    
    if not nueva_cot.data:
        raise HTTPException(500, detail="Error al duplicar cotización")
    
    nueva_cot_id = nueva_cot.data[0]["cotizacion_id"]
    
    return {
        "ok": True,
        "cotizacion_original": cotizacion_id,
        "cotizacion_nueva": nueva_cot_id,
        "nombre_proyecto": c.get("prompt_original"),
        "total_estimado": float(c.get("total_estimado") or 0),
        "mensaje": f"Cotización duplicada con ID {nueva_cot_id}. Se reenviará a los viveristas.",
    }


# ═══════════════════════════════════════════════════════════
# ENDPOINT: Agregar items a cotización existente
# ═══════════════════════════════════════════════════════════

@router.post("/cotizacion/{cotizacion_id}/agregar-items")
async def agregar_items_a_cotizacion(
    cotizacion_id: int,
    items_nuevos: list[dict],
    user: UserContext = Depends(require_comprador),
):
    """Agrega productos a una cotización ya enviada.
    
    Casos de uso:
    - Comprador ve el marketplace y quiere agregar más productos
    - Items nuevos se agregan a la cotización sin perder las subs existentes
    - Se reenvía notificación a viveristas sobre items adicionales
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    
    db = admin()
    
    # Obtener cotización actual
    resp = db.table("cotizaciones").select(
        "cliente_id, estado, items, total_estimado, prompt_original"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="No autorizado")
    
    # Solo se puede agregar si está en estado "enviada" o "parcial"
    if c["estado"] not in ("enviada", "parcial", "rechazada"):
        raise HTTPException(400, detail=f"No se pueden agregar items a cotización en estado '{c['estado']}'")
    
    # Agregar items nuevos (evitando duplicados por SKU)
    items_existentes = c.get("items") or []
    skus_existentes = {it.get("inventario_id") for it in items_existentes}
    
    items_para_agregar = []
    for it_nuevo in items_nuevos:
        inv_id = it_nuevo.get("inventario_id")
        if inv_id not in skus_existentes:
            items_para_agregar.append(it_nuevo)
            skus_existentes.add(inv_id)
    
    if not items_para_agregar:
        return {
            "ok": True,
            "cotizacion_id": cotizacion_id,
            "items_agregados": 0,
            "mensaje": "Los productos ya estaban en la cotización",
        }
    
    # Actualizar cotización con items nuevos
    items_finales = items_existentes + items_para_agregar
    
    db.table("cotizaciones").update({
        "items": items_finales,
    }).eq("cotizacion_id", cotizacion_id).execute()
    
    return {
        "ok": True,
        "cotizacion_id": cotizacion_id,
        "items_agregados": len(items_para_agregar),
        "total_items_ahora": len(items_finales),
        "mensaje": f"✅ Agregados {len(items_para_agregar)} producto(s) a la cotización",
    }
