"""Endpoints públicos sin auth.

NOTA: El dashboard inversor antes vivía aquí (/api/public/flywheel) sin auth.
Ahora se movió a /api/inversores/flywheel y requiere cookie de invitación.

Endpoints vigentes:
- GET /api/public/health
- GET /api/public/marketplace                → vitrina pública (lista)
- GET /api/public/marketplace/item/{inv_id}  → vitrina pública (detalle)
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.supabase import admin


router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/health")
async def public_health():
    return {"ok": True, "service": "public-api", "ready": True}


# ─────────────────── VITRINA PÚBLICA: LISTADO ───────────────────

@router.get("/marketplace")
async def listar_marketplace_publico(
    q: Optional[str] = None,
    municipio: Optional[str] = None,
    altura_min: int = 0,
    cantidad_min: int = 1,
    lat: float = 4.9195,
    lon: float = -74.0270,
    radio_km: float = 50.0,
    limite: int = Query(20, le=100),
):
    """Vitrina pública del marketplace. SIN auth.

    Cualquier visitante puede ver: planta, foto, precio mayorista,
    stock, vivero (nombre), municipio, distancia.

    NO se exponen contactos del vivero (teléfono, WhatsApp) — eso requiere
    autenticación vía /api/marketplace.
    """
    db = admin()

    if q and q.strip():
        # Búsqueda por nombre usando función SQL geo
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


# ─────────────────── VITRINA PÚBLICA: DETALLE ───────────────────

@router.get("/marketplace/item/{inventario_id}")
async def detalle_item_publico(inventario_id: int):
    """Detalle público de un item del marketplace. SIN auth.

    Devuelve todos los datos botánicos + precio + stock + vivero (nombre, ciudad, coords).
    NO devuelve teléfono ni WhatsApp del vivero — para eso hay que loguearse.
    """
    db = admin()
    resp = db.table("inventario").select(
        "inventario_id, planta_id, altura_cm, precio_mayorista, precio_detal, "
        "stock, unidad_medida, estado_planta, foto_ia_url, notas, vivero_id, "
        "plantas(nombre_comun, nombre_cientifico, familia_botanica, requerimientos_ia, clima_ideal), "
        "viveros(nombre_vivero, ciudad, latitud, longitud)"
    ).eq("inventario_id", inventario_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Item no encontrado")
    return {"ok": True, "item": resp.data[0]}
