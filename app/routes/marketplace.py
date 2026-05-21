"""Marketplace (para compradores) + cotizaciones.

Endpoints:
- GET  /api/marketplace                            → lista/busca inventario disponible
- GET  /api/marketplace/item/{inv_id}              → detalle de un item

Cotizaciones (multi-proyecto):
- GET  /api/marketplace/cotizacion/proyectos       → lista TODOS los proyectos del comprador
- GET  /api/marketplace/cotizacion/{id}            → detalle de UN proyecto (items enriquecidos)
- POST /api/marketplace/cotizacion                 → agregar items (3 modos: cotizacion_id | nombre_proyecto | legacy)
- PATCH /api/marketplace/cotizacion/{id}           → renombrar proyecto (solo borradores)
- DELETE /api/marketplace/cotizacion/{id}          → eliminar proyecto entero
- DELETE /api/marketplace/cotizacion/item/{inv_id} → quitar item (opcional ?cotizacion_id=X)

Compat:
- GET /api/marketplace/cotizacion/borrador → último borrador del comprador (legacy)
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import UserContext, require_comprador, require_user
from app.schemas.catalog import InventarioItem
from app.schemas.transactions import (
    CotizacionRequest, CotizacionResponse, RenameProyectoRequest,
)
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


# ═══════════════════════════════════════════════════════════
# COTIZACIONES — helpers y endpoints
# ═══════════════════════════════════════════════════════════

def _validar_items_y_calcular(db, req_items) -> tuple[list[dict], float]:
    """Valida disponibilidad/stock de cada item y calcula subtotales.

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


def _merge_items_y_actualizar(
    db, borrador: dict, items_nuevos: list[dict],
    notas_nuevas: Optional[str] = None,
    nombre_nuevo: Optional[str] = None,
) -> CotizacionResponse:
    """Agrega `items_nuevos` al borrador existente. Items con mismo inventario_id se suman."""
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
    """Agrega info de planta/vivero a cada item del JSONB. Para mostrar en UI."""
    if not items_raw:
        return []
    inventario_ids = [it.get("inventario_id") for it in items_raw if it.get("inventario_id")]
    inv_map: dict[int, dict] = {}
    if inventario_ids:
        inv_resp = db.table("inventario").select(
            "inventario_id, foto_ia_url, stock, estado_planta, "
            "plantas(nombre_comun, nombre_cientifico), "
            "viveros(vivero_id, nombre_vivero, ciudad)"
        ).in_("inventario_id", inventario_ids).execute()
        inv_map = {r["inventario_id"]: r for r in (inv_resp.data or [])}

    enriquecidos = []
    for it in items_raw:
        inv = inv_map.get(it.get("inventario_id"), {})
        planta = inv.get("plantas") or {}
        vivero = inv.get("viveros") or {}
        enriquecidos.append({
            **it,
            "nombre_comun": planta.get("nombre_comun"),
            "nombre_cientifico": planta.get("nombre_cientifico"),
            "foto_ia_url": inv.get("foto_ia_url"),
            "stock_disponible": inv.get("stock"),
            "vivero_id": (vivero or {}).get("vivero_id"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "ciudad_vivero": vivero.get("ciudad"),
        })
    return enriquecidos


# ─────────────────── POST: crear / agregar items ───────────────────

@router.post("/cotizacion", response_model=CotizacionResponse)
async def crear_o_agregar_a_borrador(
    req: CotizacionRequest,
    user: UserContext = Depends(require_comprador),
):
    """Crea un borrador nuevo o agrega items a un borrador existente.

    Modos de operación (decide el frontend según el flujo del usuario):

    1. EXPLÍCITO con `cotizacion_id`: agrega items al borrador con ese ID.
       Valida que el borrador exista, pertenezca al comprador, y esté en
       estado 'borrador' (no se permite modificar enviada/aceptada/etc).

    2. EXPLÍCITO con `nombre_proyecto`: crea un NUEVO borrador con ese nombre.
       Es el flujo cuando el usuario elige "Crear nuevo proyecto" en el modal.

    3. LEGACY (sin cotizacion_id ni nombre_proyecto): find-or-create.
       Busca el borrador más reciente del comprador y le agrega los items.
       Si no tiene ninguno, crea uno nuevo (sin nombre si `proyecto` tampoco
       viene en el request). Preservado para que el frontend pre-modal del
       marketplace_detalle.html siga funcionando hasta que se actualice.
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    if req.cotizacion_id is not None and req.nombre_proyecto is not None:
        raise HTTPException(400, detail="Especificá solo cotizacion_id O nombre_proyecto, no ambos")

    db = admin()
    items_nuevos, total_nuevo = _validar_items_y_calcular(db, req.items)

    # ── Modo 1: cotizacion_id explícito ──
    if req.cotizacion_id is not None:
        existente = db.table("cotizaciones").select(
            "cotizacion_id, cliente_id, estado, items, total_estimado"
        ).eq("cotizacion_id", req.cotizacion_id).limit(1).execute()

        if not existente.data:
            raise HTTPException(404, detail="Proyecto no encontrado")
        b = existente.data[0]
        if b["cliente_id"] != user.cliente_id:
            # No revelar existencia ajena
            raise HTTPException(404, detail="Proyecto no encontrado")
        if b["estado"] != "borrador":
            raise HTTPException(
                400,
                detail=f"No se pueden agregar items a un proyecto en estado '{b['estado']}'",
            )

        return _merge_items_y_actualizar(db, b, items_nuevos)

    # ── Modo 2: nombre_proyecto explícito (crear nuevo) ──
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
        return CotizacionResponse(
            ok=True,
            cotizacion_id=cotizacion_id,
            total_cop=total_nuevo,
            estado="borrador",
        )

    # ── Modo 3 (legacy): find-or-create ──
    existente = db.table("cotizaciones").select(
        "cotizacion_id, items, total_estimado, notas_cliente, prompt_original"
    ).eq("cliente_id", user.cliente_id).eq("estado", "borrador").order(
        "fecha_creacion", desc=True
    ).limit(1).execute()

    if existente.data:
        return _merge_items_y_actualizar(
            db, existente.data[0], items_nuevos,
            notas_nuevas=req.notas,
            nombre_nuevo=req.proyecto,
        )

    # No tiene borrador → crear uno nuevo
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


# ─────────────────── GET: lista de proyectos (dashboard) ───────────────────

@router.get("/cotizacion/proyectos")
async def listar_proyectos(
    user: UserContext = Depends(require_comprador),
):
    """Lista TODOS los proyectos del comprador (todos los estados).

    Resumen para cards del dashboard "Mis Proyectos". NO incluye items
    detallados — para eso usar GET /cotizacion/{id}.
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    db = admin()
    resp = db.table("cotizaciones").select(
        "cotizacion_id, prompt_original, estado, total_estimado, items, "
        "fecha_creacion, fecha_vencimiento, fecha_conversion, notas_cliente"
    ).eq("cliente_id", user.cliente_id).order("fecha_creacion", desc=True).execute()

    proyectos = []
    for r in resp.data or []:
        items = r.get("items") or []
        proyectos.append({
            "cotizacion_id": r["cotizacion_id"],
            "nombre_proyecto": r.get("prompt_original"),  # null → UI muestra "Sin nombre"
            "estado": r["estado"],
            "total_estimado": float(r.get("total_estimado") or 0),
            "num_items": sum(it.get("cantidad", 0) for it in items),
            "num_items_distintos": len(items),
            "fecha_creacion": str(r.get("fecha_creacion") or ""),
            "fecha_vencimiento": str(r.get("fecha_vencimiento") or ""),
            "fecha_conversion": str(r.get("fecha_conversion")) if r.get("fecha_conversion") else None,
            "notas_cliente": r.get("notas_cliente"),
        })

    return {"ok": True, "proyectos": proyectos, "total": len(proyectos)}


# ─────────────────── GET: detalle de UN proyecto ───────────────────

@router.get("/cotizacion/{cotizacion_id}")
async def obtener_cotizacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Detalle de UN proyecto con items enriquecidos (foto, planta, vivero).

    Verifica ownership: si el cotizacion_id no es del comprador, devuelve 404
    (no revelamos existencia).
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    db = admin()
    resp = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, prompt_original, estado, items, total_estimado, "
        "fecha_creacion, fecha_vencimiento, fecha_conversion, notas_cliente"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")

    items_enriquecidos = _enriquecer_items(db, c.get("items") or [])

    return {
        "ok": True,
        "cotizacion": {
            "cotizacion_id": c["cotizacion_id"],
            "nombre_proyecto": c.get("prompt_original"),
            "estado": c["estado"],
            "items": items_enriquecidos,
            "total_estimado": float(c.get("total_estimado") or 0),
            "num_items": sum(it.get("cantidad", 0) for it in items_enriquecidos),
            "num_viveros_distintos": len({it.get("vivero_id") for it in items_enriquecidos if it.get("vivero_id")}),
            "notas_cliente": c.get("notas_cliente"),
            "fecha_creacion": str(c.get("fecha_creacion") or ""),
            "fecha_vencimiento": str(c.get("fecha_vencimiento") or ""),
            "fecha_conversion": str(c.get("fecha_conversion")) if c.get("fecha_conversion") else None,
        },
    }


# ─────────────────── PATCH: renombrar proyecto ───────────────────

@router.patch("/cotizacion/{cotizacion_id}")
async def renombrar_proyecto(
    cotizacion_id: int,
    req: RenameProyectoRequest,
    user: UserContext = Depends(require_comprador),
):
    """Renombra un proyecto. Solo permitido si está en estado 'borrador'."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    db = admin()
    resp = db.table("cotizaciones").select(
        "cliente_id, estado"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    if c["estado"] != "borrador":
        raise HTTPException(
            400,
            detail=f"No se puede renombrar una cotización en estado '{c['estado']}'. Solo borradores.",
        )

    db.table("cotizaciones").update({
        "prompt_original": req.nombre_proyecto,
    }).eq("cotizacion_id", cotizacion_id).execute()

    return {"ok": True, "cotizacion_id": cotizacion_id, "nombre_proyecto": req.nombre_proyecto}


# ─────────────────── DELETE: eliminar proyecto entero ───────────────────

@router.delete("/cotizacion/{cotizacion_id}")
async def eliminar_proyecto(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Elimina un proyecto entero. Solo permitido en borrador/vencida/rechazada."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    db = admin()
    resp = db.table("cotizaciones").select(
        "cliente_id, estado"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    if c["estado"] not in ("borrador", "vencida", "rechazada"):
        raise HTTPException(
            400,
            detail=f"No se puede eliminar una cotización en estado '{c['estado']}'.",
        )

    db.table("cotizaciones").delete().eq("cotizacion_id", cotizacion_id).execute()
    return {"ok": True, "cotizacion_id": cotizacion_id, "eliminado": True}


# ─────────────────── GET: borrador activo (LEGACY) ───────────────────

@router.get("/cotizacion/borrador")
async def obtener_borrador(
    user: UserContext = Depends(require_comprador),
):
    """[Legacy] Retorna el borrador MÁS RECIENTE del comprador.

    Mantenido para compat con el dashboard del comprador previo a multi-proyecto.
    Una vez migrado el dashboard (PR3) a /cotizacion/proyectos + /cotizacion/{id},
    se puede deprecar.
    """
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


# ─────────────────── DELETE: quitar item ───────────────────

@router.delete("/cotizacion/item/{inventario_id}")
async def quitar_item_borrador(
    inventario_id: int,
    cotizacion_id: Optional[int] = Query(None, description="Si se omite, opera sobre el borrador más reciente (legacy)"),
    user: UserContext = Depends(require_comprador),
):
    """Quita un item del borrador.

    - Con `?cotizacion_id=X` → modo explícito sobre ese borrador (valida ownership + estado).
    - Sin query param → modo legacy, opera sobre el borrador más reciente del comprador.

    Si el borrador queda vacío tras la remoción, se BORRA el borrador entero
    (no dejamos cotizaciones huérfanas).
    """
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")

    db = admin()

    if cotizacion_id is not None:
        # Modo explícito
        resp = db.table("cotizaciones").select(
            "cotizacion_id, cliente_id, estado, items"
        ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(404, detail="Cotización no encontrada")
        b = resp.data[0]
        if b["cliente_id"] != user.cliente_id:
            raise HTTPException(404, detail="Cotización no encontrada")
        if b["estado"] != "borrador":
            raise HTTPException(
                400,
                detail=f"No se puede modificar una cotización en estado '{b['estado']}'",
            )
    else:
        # Modo legacy: borrador más reciente
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
        # Carrito vacío → borrar el borrador
        db.table("cotizaciones").delete().eq("cotizacion_id", b["cotizacion_id"]).execute()
        return {
            "ok": True,
            "cotizacion_id": b["cotizacion_id"],
            "eliminado": True,
            "borrador": None,
        }

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
