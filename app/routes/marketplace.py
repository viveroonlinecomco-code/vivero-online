"""Marketplace (para compradores) + cotizaciones.

- GET  /api/marketplace                 → lista/busca inventario disponible
- GET  /api/marketplace/item/{inv_id}   → detalle de un item
- POST /api/marketplace/cotizacion      → crear cotización desde items
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import UserContext, require_comprador, require_user
from app.schemas.catalog import InventarioItem
from app.schemas.transactions import CotizacionRequest, CotizacionResponse
from app.services.supabase import admin


router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


# ─────────────────── LISTAR MARKETPLACE ───────────────────

@router.get("")
async def listar_marketplace(
    q: Optional[str] = None,
    municipio: Optional[str] = None,
    altura_min: int = 0,
    cantidad_min: int = 1,
    lat: float = 4.9195,
    lon: float = -74.0270,
    radio_km: float = 50.0,
    limite: int = Query(20, le=100),
    user: UserContext = Depends(require_user),
):
    """Búsqueda geo-espacial + texto en el marketplace.

    Usa la función `buscar_plantas_cercanas` de PostGIS si hay término de búsqueda;
    si no, lista inventario disponible ordenado por fecha.
    """
    db = admin()

    if q and q.strip():
        # Búsqueda por nombre usando la función SQL geo
        resp = db.rpc("buscar_plantas_cercanas", {
            "p_nombre_planta": q.strip(),
            "p_altura_min_cm": altura_min,
            "p_radio_km": radio_km,
            "p_lat": lat,
            "p_lon": lon,
            "p_cantidad_min": cantidad_min,
            "p_limite": limite,
        }).execute()

        items = []
        for r in resp.data or []:
            items.append({
                "inventario_id": r["inventario_id"],
                "planta_id": r["planta_id"],
                "nombre_comun": r["nombre_comun"],
                "nombre_cientifico": r.get("nombre_cientifico"),
                "foto_ia_url": r.get("foto_ia_url"),
                "precio_mayorista": float(r.get("precio_mayorista") or 0),
                "stock": r["stock"],
                "altura_cm": r["altura_cm"],
                "vivero_id": r["vivero_id"],
                "nombre_vivero": r["nombre_vivero"],
                "distancia_km": float(r.get("distancia_km") or 0),
            })
        return {"ok": True, "items": items, "total": len(items)}

    # Sin búsqueda: listado general de disponibles
    query = db.table("inventario").select(
        "inventario_id, planta_id, altura_cm, precio_mayorista, "
        "stock, unidad_medida, foto_ia_url, vivero_id, "
        "plantas(nombre_comun, nombre_cientifico), "
        "viveros(nombre_vivero, ciudad)"
    ).eq("estado_planta", "disponible").gte("stock", cantidad_min).gte("altura_cm", altura_min)

    if municipio:
        # Filtrar por viveros en ese municipio requeriría join side;
        # aquí hacemos 2 queries: viveros del municipio + inventario
        v_resp = db.table("viveros").select("vivero_id").eq("ciudad", municipio).execute()
        vivero_ids = [v["vivero_id"] for v in (v_resp.data or [])]
        if not vivero_ids:
            return {"ok": True, "items": [], "total": 0}
        query = query.in_("vivero_id", vivero_ids)

    resp = query.limit(limite).execute()
    items = []
    for r in resp.data or []:
        planta = r.get("plantas") or {}
        vivero = r.get("viveros") or {}
        items.append({
            "inventario_id": r["inventario_id"],
            "planta_id": r["planta_id"],
            "nombre_comun": planta.get("nombre_comun", "Sin nombre"),
            "nombre_cientifico": planta.get("nombre_cientifico"),
            "foto_ia_url": r.get("foto_ia_url"),
            "precio_mayorista": float(r.get("precio_mayorista") or 0),
            "stock": r.get("stock") or 0,
            "altura_cm": r.get("altura_cm") or 0,
            "unidad_medida": r.get("unidad_medida"),
            "vivero_id": r.get("vivero_id"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "municipio": vivero.get("ciudad"),
        })
    return {"ok": True, "items": items, "total": len(items)}


# ─────────────────── DETALLE ITEM ───────────────────

@router.get("/item/{inventario_id}")
async def detalle_item(
    inventario_id: int,
    user: UserContext = Depends(require_user),
):
    db = admin()
    resp = db.table("inventario").select(
        "inventario_id, planta_id, altura_cm, precio_mayorista, precio_detal, "
        "stock, unidad_medida, estado_planta, foto_ia_url, notas, vivero_id, "
        "plantas(nombre_comun, nombre_cientifico, familia_botanica, requerimientos_ia, clima_ideal), "
        "viveros(nombre_vivero, ciudad, latitud, longitud, telefono, whatsapp_numero, historia, foto_url, fotos_galeria, direccion)"
    ).eq("inventario_id", inventario_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Item no encontrado")
    return {"ok": True, "item": resp.data[0]}


# ─────────────────── COTIZACIÓN ───────────────────

@router.post("/cotizacion", response_model=CotizacionResponse)
async def crear_cotizacion(
    req: CotizacionRequest,
    user: UserContext = Depends(require_comprador),
):
    """Crea una cotización con múltiples items del marketplace."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    db = admin()

    # 1. Validar items y calcular total
    total_cop = 0.0
    items_validados = []
    for item in req.items:
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
        total_cop += subtotal
        items_validados.append({
            "inventario_id": item.inventario_id,
            "cantidad": item.cantidad,
            "precio_unitario": float(i["precio_mayorista"]),
            "subtotal": subtotal,
        })

    # 2. Crear cotización (tabla cotizaciones - columnas reales)
    cot_resp = db.table("cotizaciones").insert({
        "cliente_id": user.cliente_id,
        "total_estimado": total_cop,
        "estado": "pendiente",
        "notas_cliente": req.notas,
        "prompt_original": req.proyecto,
        "items": items_validados,
        "generada_por_ia": False,
    }).execute()
    cotizacion_id = cot_resp.data[0]["cotizacion_id"]

    return CotizacionResponse(
        ok=True,
        cotizacion_id=cotizacion_id,
        total_cop=total_cop,
        estado="pendiente",
    )
