"""Marketplace (para compradores) + cotizaciones.

- GET  /api/marketplace                        → lista/busca inventario disponible
- GET  /api/marketplace/item/{inv_id}          → detalle de un item
- POST /api/marketplace/cotizacion             → agregar item(s) al borrador (carrito)
- GET  /api/marketplace/cotizacion/borrador    → obtener el borrador activo
- DELETE /api/marketplace/cotizacion/item/{id} → quitar item del borrador
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


# ─────────────────── COTIZACIÓN — agregar al carrito ───────────────────

def _validar_items_y_calcular(db, req_items) -> tuple[list[dict], float]:
    """Valida disponibilidad/stock y calcula subtotales.

    Retorna (lista de items normalizados, total).
    Lanza HTTPException si algún item no es válido.
    """
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


@router.post("/cotizacion", response_model=CotizacionResponse)
async def crear_o_agregar_a_borrador(
    req: CotizacionRequest,
    user: UserContext = Depends(require_comprador),
):
    """Agrega items al borrador activo del comprador (su carrito).

    Modelo: cada comprador tiene UNA cotización en borrador a la vez.
    - Si el comprador ya tiene un borrador → AGREGA los items al existente.
      Si el mismo inventario_id ya estaba en el carrito, suma cantidades.
    - Si no tiene borrador → crea uno nuevo.

    Cuando el comprador haga checkout efectivo + pago confirmado, el borrador
    transiciona a 'enviada' y se notifica a los viveros con stock.

    Estados válidos en BD (cotizaciones_estado_check):
    borrador | enviada | aceptada | rechazada | vencida | convertida
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    db = admin()
    items_nuevos, total_nuevo = _validar_items_y_calcular(db, req.items)

    # ¿Tiene borrador activo?
    existente = db.table("cotizaciones").select(
        "cotizacion_id, items, total_estimado, notas_cliente, prompt_original"
    ).eq("cliente_id", user.cliente_id).eq("estado", "borrador").limit(1).execute()

    if existente.data:
        # ── Engordar el borrador existente ──
        b = existente.data[0]
        cotizacion_id = b["cotizacion_id"]
        items_combinados = list(b.get("items") or [])

        # Merge por inventario_id: si ya estaba, sumar; si no, append
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

        db.table("cotizaciones").update({
            "items": items_combinados,
            "total_estimado": total_combinado,
            # Sobreescribir solo si el request los trae
            "notas_cliente": req.notas if req.notas else b.get("notas_cliente"),
            "prompt_original": req.proyecto if req.proyecto else b.get("prompt_original"),
        }).eq("cotizacion_id", cotizacion_id).execute()

        return CotizacionResponse(
            ok=True,
            cotizacion_id=cotizacion_id,
            total_cop=total_combinado,
            estado="borrador",
        )

    # ── Crear borrador nuevo ──
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

    return CotizacionResponse(
        ok=True,
        cotizacion_id=cotizacion_id,
        total_cop=total_nuevo,
        estado="borrador",
    )


# ─────────────────── COTIZACIÓN — obtener borrador (carrito) ───────────────────

@router.get("/cotizacion/borrador")
async def obtener_borrador(
    user: UserContext = Depends(require_comprador),
):
    """Retorna el borrador activo del comprador con sus items enriquecidos.

    Si no hay borrador, retorna `{"ok": True, "borrador": None}` (200, no 404)
    para que el frontend pueda renderizar "carrito vacío" sin manejar error.
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    db = admin()
    resp = db.table("cotizaciones").select(
        "cotizacion_id, items, total_estimado, fecha_creacion, "
        "fecha_vencimiento, notas_cliente, prompt_original"
    ).eq("cliente_id", user.cliente_id).eq("estado", "borrador").limit(1).execute()

    if not resp.data:
        return {"ok": True, "borrador": None}

    b = resp.data[0]
    items_raw = b.get("items") or []

    # Enriquecer items con info de planta/vivero (lo que el dashboard necesita renderizar)
    inventario_ids = [it.get("inventario_id") for it in items_raw if it.get("inventario_id")]
    inv_map: dict[int, dict] = {}
    if inventario_ids:
        inv_resp = db.table("inventario").select(
            "inventario_id, foto_ia_url, stock, estado_planta, "
            "plantas(nombre_comun, nombre_cientifico), "
            "viveros(vivero_id, nombre_vivero, ciudad)"
        ).in_("inventario_id", inventario_ids).execute()
        inv_map = {r["inventario_id"]: r for r in (inv_resp.data or [])}

    items_enriquecidos = []
    for it in items_raw:
        inv = inv_map.get(it.get("inventario_id"), {})
        planta = inv.get("plantas") or {}
        vivero = inv.get("viveros") or {}
        items_enriquecidos.append({
            **it,
            "nombre_comun": planta.get("nombre_comun"),
            "nombre_cientifico": planta.get("nombre_cientifico"),
            "foto_ia_url": inv.get("foto_ia_url"),
            "stock_disponible": inv.get("stock"),
            "vivero_id": (vivero or {}).get("vivero_id"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "ciudad_vivero": vivero.get("ciudad"),
        })

    return {
        "ok": True,
        "borrador": {
            "cotizacion_id": b["cotizacion_id"],
            "items": items_enriquecidos,
            "total_estimado": float(b.get("total_estimado") or 0),
            "num_items": sum(it.get("cantidad", 0) for it in items_enriquecidos),
            "num_viveros_distintos": len({it.get("vivero_id") for it in items_enriquecidos if it.get("vivero_id")}),
            "notas_cliente": b.get("notas_cliente"),
            "prompt_original": b.get("prompt_original"),
            "fecha_creacion": str(b.get("fecha_creacion") or ""),
            "fecha_vencimiento": str(b.get("fecha_vencimiento") or ""),
        },
    }


# ─────────────────── COTIZACIÓN — quitar item del borrador ───────────────────

@router.delete("/cotizacion/item/{inventario_id}")
async def quitar_item_borrador(
    inventario_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Quita un item del borrador activo.

    Si era el último item, BORRA el borrador entero (no dejamos cotizaciones
    vacías ensuciando la BD).
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    db = admin()
    resp = db.table("cotizaciones").select(
        "cotizacion_id, items"
    ).eq("cliente_id", user.cliente_id).eq("estado", "borrador").limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="No tienes un borrador activo")

    b = resp.data[0]
    items_actuales = b.get("items") or []
    items_filtrados = [it for it in items_actuales if it.get("inventario_id") != inventario_id]

    if len(items_filtrados) == len(items_actuales):
        raise HTTPException(404, detail=f"Item {inventario_id} no está en tu borrador")

    if not items_filtrados:
        # Carrito vacío → borrar el borrador
        db.table("cotizaciones").delete().eq("cotizacion_id", b["cotizacion_id"]).execute()
        return {"ok": True, "borrador": None}

    total_nuevo = sum(float(it.get("subtotal") or 0) for it in items_filtrados)
    db.table("cotizaciones").update({
        "items": items_filtrados,
        "total_estimado": total_nuevo,
    }).eq("cotizacion_id", b["cotizacion_id"]).execute()

    return {
        "ok": True,
        "cotizacion_id": b["cotizacion_id"],
        "total_estimado": total_nuevo,
        "items_restantes": len(items_filtrados),
    }
