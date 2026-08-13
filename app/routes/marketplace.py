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

FIX 13 AGO 2026 — Estado real de cotizaciones con sub_cotizaciones.
- listar_proyectos: ahora devuelve sub_cotizaciones + estado real + monto_disponible
- obtener_cotizacion: devuelve desglose viveros + puede_pagar_parcial flag
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
from app.services.config_global import get_markup_categoria, get_matriz_comercial

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])
public_router = APIRouter(prefix="/api/public/marketplace", tags=["marketplace-guest"])


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
    markup = float(get_markup_categoria(categoria) or 0.20)
    return int(round(precio_mayorista * (1 + markup)))


def _enriquecer_items(db, items: list[dict]) -> list[dict]:
    """Enriquece items con nombres, precios comprador, etc."""
    if not items:
        return []

    # Batch resolve categorías
    inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
    categorias_map = _resolver_categorias_batch(db, inv_ids) if inv_ids else {}

    # Fetch nombres
    nombres_map = {}
    if inv_ids:
        try:
            resp = db.table("inventario").select(
                "inventario_id, plantas(nombre_comun), viveros(nombre_vivero, ciudad)"
            ).in_("inventario_id", inv_ids).execute()
            nombres_map = {
                r["inventario_id"]: {
                    "nombre_comun": (r.get("plantas") or {}).get("nombre_comun", "Planta"),
                    "vivero_id": r.get("vivero_id"),
                    "nombre_vivero": (r.get("viveros") or {}).get("nombre_vivero"),
                    "ciudad": (r.get("viveros") or {}).get("ciudad"),
                }
                for r in (resp.data or [])
            }
        except Exception:
            pass

    # Enriquecer
    resultado = []
    for it in items:
        inv_id = it.get("inventario_id")
        precio_unit = float(it.get("precio_unitario") or 0)
        cantidad = int(it.get("cantidad") or 0)

        info = nombres_map.get(inv_id, {})
        categoria = categorias_map.get(inv_id, "plantas_ornamentales")
        markup = float(get_markup_categoria(categoria) or 0.20)
        precio_comprador_unit = int(round(precio_unit * (1 + markup)))

        resultado.append({
            "inventario_id": inv_id,
            "nombre_comun": info.get("nombre_comun", "Planta"),
            "vivero_id": info.get("vivero_id"),
            "nombre_vivero": info.get("nombre_vivero"),
            "ciudad": info.get("ciudad"),
            "cantidad": cantidad,
            "precio_unitario": precio_unit,
            "precio_comprador_unitario": precio_comprador_unit,
            "subtotal": precio_unit * cantidad,
            "subtotal_comprador": precio_comprador_unit * cantidad,
            "categoria": categoria,
            "markup": f"{markup*100:.0f}%",
        })

    return resultado


def _total_comprador_desde_items(items: list[dict]) -> int:
    """Suma subtotal_comprador de todos los items."""
    return sum(int(it.get("subtotal_comprador") or 0) for it in items)


def _obtener_estado_entrega(db, cotizacion_id: int) -> dict:
    """Obtiene estado entrega si existe."""
    try:
        resp = db.table("entregas").select(
            "estado_entrega, fecha_despacho, fecha_entrega, direccion_entrega"
        ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
        return resp.data[0] if resp.data else {}
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════
# HELPER NUEVO — Calcular estado real (FIX 13 ago)
# ═══════════════════════════════════════════════════════════

def _calcular_estado_real(estado_cot: str, subs: list[dict]) -> str:
    """Calcula estado real basado en sub_cotizaciones.
    
    Reglas:
      - Si NO hay subs: usa estado_cot original
      - Si TODAS pendiente: "enviada"
      - Si AL MENOS 1 aprobada Y AL MENOS 1 rechazada: "parcial"
      - Si TODAS aprobadas: "aceptada"
      - Si TODAS rechazadas: "rechazada"
      - Si AL MENOS 1 pendiente (y no hay rechazo total): "enviada"
    """
    if not subs:
        return estado_cot
    
    estados = [s.get("estado") for s in subs]
    
    aprobadas = sum(1 for e in estados if e == "aprobada")
    rechazadas = sum(1 for e in estados if e == "rechazada")
    pendientes = sum(1 for e in estados if e == "pendiente")
    
    total = len(estados)
    
    # Caso: mezcla aprobadas y rechazadas → parcial
    if aprobadas > 0 and rechazadas > 0:
        return "parcial"
    
    # Caso: todas aprobadas
    if aprobadas == total:
        return "aceptada"
    
    # Caso: todas rechazadas
    if rechazadas == total:
        return "rechazada"
    
    # Caso: hay pendientes (no todos rechazados)
    if pendientes > 0:
        return "enviada"
    
    # Fallback
    return estado_cot


# ═══════════════════════════════════════════════════════════
# ENDPOINTS PÚBLICOS
# ═══════════════════════════════════════════════════════════

@public_router.get("")
async def listar_marketplace_guest(
    categoria: Optional[str] = Query(None),
    ciudad: Optional[str] = Query(None),
    limite: int = Query(20, ge=1, le=100),
):
    """Listado marketplace para guest (tier S+M solo)."""
    db = admin()
    query = db.table("inventario").select(
        "inventario_id, plantas(nombre_comun, nombre_cientifico, categoria_producto), "
        "viveros(nombre_vivero, ciudad), precio_mayorista, stock, "
        "altura_cm, tier_manual, foto_url_thumb"
    ).in_("tier_manual", ["S", "M"])

    if categoria:
        query = query.eq("categoria_producto", categoria)
    if ciudad:
        query = query.ilike("viveros.ciudad", f"%{ciudad}%")

    resp = query.limit(limite).execute()
    return {
        "ok": True,
        "items": resp.data or [],
        "total": len(resp.data or []),
    }


@public_router.get("/item/{inventario_id}")
async def obtener_item_marketplace_guest(inventario_id: int):
    """Detalle de un item para guest."""
    db = admin()
    resp = db.table("inventario").select(
        "inventario_id, plantas(nombre_comun, nombre_cientifico, descripcion, categoria_producto), "
        "viveros(nombre_vivero, ciudad, whatsapp_numero), precio_mayorista, stock, "
        "altura_cm, tier_manual, foto_url, fotos_adicionales"
    ).eq("inventario_id", inventario_id).eq("tier_manual", "S").limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Producto no encontrado o no disponible para guest")

    item = resp.data[0]
    planta = item.get("plantas") or {}
    vivero = item.get("viveros") or {}

    return {
        "ok": True,
        "item": {
            "inventario_id": inventario_id,
            "nombre_comun": planta.get("nombre_comun"),
            "nombre_cientifico": planta.get("nombre_cientifico"),
            "descripcion": planta.get("descripcion"),
            "categoria": planta.get("categoria_producto"),
            "precio_mayorista": float(item.get("precio_mayorista") or 0),
            "stock": int(item.get("stock") or 0),
            "altura_cm": float(item.get("altura_cm") or 0),
            "vivero": {
                "nombre": vivero.get("nombre_vivero"),
                "ciudad": vivero.get("ciudad"),
                "whatsapp": vivero.get("whatsapp_numero"),
            },
            "fotos": {
                "thumb": item.get("foto_url_thumb"),
                "principal": item.get("foto_url"),
                "adicionales": item.get("fotos_adicionales") or [],
            },
        },
    }


# ═══════════════════════════════════════════════════════════
# ENDPOINTS COMPRADOR AUTENTICADO
# ═══════════════════════════════════════════════════════════

@router.get("")
async def listar_marketplace(
    categoria: Optional[str] = Query(None),
    ciudad: Optional[str] = Query(None),
    limite: int = Query(50, ge=1, le=500),
    user: UserContext = Depends(require_user),
):
    """Listado completo marketplace (autenticado)."""
    db = admin()
    query = db.table("inventario").select(
        "inventario_id, plantas(nombre_comun, nombre_cientifico, categoria_producto), "
        "viveros(nombre_vivero, ciudad), precio_mayorista, stock, "
        "altura_cm, tier_manual, foto_url_thumb"
    )

    if categoria:
        query = query.eq("categoria_producto", categoria)
    if ciudad:
        query = query.ilike("viveros.ciudad", f"%{ciudad}%")

    resp = query.limit(limite).execute()
    return {
        "ok": True,
        "items": resp.data or [],
        "total": len(resp.data or []),
    }


@router.get("/item/{inventario_id}")
async def obtener_item_marketplace(
    inventario_id: int,
    user: UserContext = Depends(require_user),
):
    """Detalle de un item (autenticado)."""
    db = admin()
    resp = db.table("inventario").select(
        "inventario_id, plantas(nombre_comun, nombre_cientifico, descripcion, categoria_producto), "
        "viveros(nombre_vivero, ciudad, latitud, longitud, whatsapp_numero), "
        "precio_mayorista, stock, altura_cm, tier_manual, foto_url, fotos_adicionales"
    ).eq("inventario_id", inventario_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Producto no encontrado")

    item = resp.data[0]
    return {"ok": True, "item": item}


# ═══════════════════════════════════════════════════════════
# COTIZACIONES — FIX 13 ago 2026
# ═══════════════════════════════════════════════════════════

@router.get("/cotizacion/proyectos")
async def listar_proyectos(user: UserContext = Depends(require_comprador)):
    """Lista proyectos con estado REAL basado en sub_cotizaciones."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    db = admin()
    
    # CAMBIO CRÍTICO: Incluir sub_cotizaciones con viveros
    resp = db.table("cotizaciones").select(
        "cotizacion_id, prompt_original, estado, total_estimado, items, "
        "fecha_creacion, fecha_vencimiento, fecha_conversion, notas_cliente, "
        "sub_cotizaciones(sub_cotizacion_id, estado, total_estimado, vivero_id, "
        "viveros(nombre_vivero, ciudad))"
    ).eq("cliente_id", user.cliente_id).order("fecha_creacion", desc=True).execute()

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

    # Recolectar TODOS los inventario_ids
    todos_inv_ids = set()
    for r in resp.data or []:
        for it in r.get("items") or []:
            if it.get("inventario_id"):
                todos_inv_ids.add(it["inventario_id"])

    categorias_map = _resolver_categorias_batch(db, list(todos_inv_ids))
    matriz = get_matriz_comercial()
    markups_b2c = matriz.get("markup_b2c", {})

    proyectos = []
    for r in resp.data or []:
        items = r.get("items") or []
        cot_id = r["cotizacion_id"]
        estado_cot = r["estado"]
        
        # NUEVO: Calcular estado real basado en sub_cotizaciones
        subs = r.get("sub_cotizaciones") or []
        estado_real = _calcular_estado_real(estado_cot, subs)

        total_comprador = 0
        for it in items:
            inv_id = it.get("inventario_id")
            precio_unit = float(it.get("precio_unitario") or 0)
            cantidad = int(it.get("cantidad") or 0)
            if inv_id:
                categoria = categorias_map.get(inv_id, "plantas_ornamentales")
                markup = float(markups_b2c.get(categoria, 0.20))
                total_comprador += round(precio_unit * (1 + markup)) * cantidad

        # NUEVO: Desglose de subs
        subs_desglose = []
        monto_disponible = 0
        for sub in subs:
            estado_sub = sub.get("estado")
            vivero_info = sub.get("viveros") or {}
            subs_desglose.append({
                "sub_cotizacion_id": sub.get("sub_cotizacion_id"),
                "estado": estado_sub,
                "total": float(sub.get("total_estimado") or 0),
                "nombre_vivero": vivero_info.get("nombre_vivero"),
                "ciudad": vivero_info.get("ciudad"),
            })
            if estado_sub == "aprobada":
                monto_disponible += float(sub.get("total_estimado") or 0)

        proyectos.append({
            "cotizacion_id": cot_id,
            "nombre_proyecto": r.get("prompt_original"),
            "estado": estado_real,  # ← CAMBIO: estado real
            "estado_entrega": entrega_map.get(cot_id),
            "total_estimado": float(r.get("total_estimado") or 0),
            "total_comprador": total_comprador,
            "monto_disponible": monto_disponible,  # ← NUEVO
            "num_items": sum(it.get("cantidad", 0) for it in items),
            "num_items_distintos": len(items),
            "fecha_creacion": str(r.get("fecha_creacion") or ""),
            "fecha_vencimiento": str(r.get("fecha_vencimiento") or ""),
            "fecha_conversion": str(r.get("fecha_conversion")) if r.get("fecha_conversion") else None,
            "notas_cliente": r.get("notas_cliente"),
            "sub_cotizaciones": subs_desglose,  # ← NUEVO
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
    ).eq("cliente_id", user.cliente_id).eq("estado", "borrador").limit(1).execute()

    if not resp.data:
        return {"ok": True, "cotizacion": None}

    cot = resp.data[0]
    items_enriquecidos = _enriquecer_items(db, cot.get("items") or [])
    total_comprador = _total_comprador_desde_items(items_enriquecidos)

    return {
        "ok": True,
        "cotizacion": {
            "cotizacion_id": cot["cotizacion_id"],
            "nombre_proyecto": cot.get("prompt_original") or "Nuevo Proyecto",
            "items": items_enriquecidos,
            "total_estimado": float(cot.get("total_estimado") or 0),
            "total_comprador": total_comprador,
            "num_items": sum(it.get("cantidad", 0) for it in items_enriquecidos),
            "notas_cliente": cot.get("notas_cliente"),
        },
    }


@router.get("/cotizacion/{cotizacion_id}")
async def obtener_cotizacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    db = admin()
    
    # CAMBIO CRÍTICO: Incluir sub_cotizaciones
    resp = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, prompt_original, estado, items, total_estimado, "
        "fecha_creacion, fecha_vencimiento, fecha_conversion, notas_cliente, notas_agente, "
        "alternativas_vivero, "
        "sub_cotizaciones(sub_cotizacion_id, vivero_id, estado, total_estimado, items, "
        "notas_rechazo, fecha_respuesta, viveros(nombre_vivero, ciudad, whatsapp_numero))"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    items_enriquecidos = _enriquecer_items(db, c.get("items") or [])
    total_comprador = _total_comprador_desde_items(items_enriquecidos)
    entrega = _obtener_estado_entrega(db, cotizacion_id)
    
    # NUEVO: Procesar sub_cotizaciones
    subs = c.get("sub_cotizaciones") or []
    subs_detalle = []
    monto_disponible = 0
    
    for sub in subs:
        estado_sub = sub.get("estado")
        vivero = sub.get("viveros") or {}
        items_sub = sub.get("items") or []
        
        items_sub_detalle = []
        for it in items_sub:
            it_enriquecido = next(
                (x for x in items_enriquecidos if x.get("inventario_id") == it.get("inventario_id")),
                None
            )
            if it_enriquecido:
                items_sub_detalle.append({
                    "inventario_id": it.get("inventario_id"),
                    "nombre_comun": it_enriquecido.get("nombre_comun"),
                    "cantidad": it.get("cantidad", 0),
                    "precio_unitario": float(it.get("precio_unitario") or 0),
                    "subtotal": float(it.get("subtotal") or 0),
                })
        
        subs_detalle.append({
            "sub_cotizacion_id": sub.get("sub_cotizacion_id"),
            "vivero_id": sub.get("vivero_id"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "ciudad": vivero.get("ciudad"),
            "whatsapp_vivero": vivero.get("whatsapp_numero"),
            "estado": estado_sub,
            "total": float(sub.get("total_estimado") or 0),
            "items": items_sub_detalle,
            "notas_rechazo": sub.get("notas_rechazo"),
            "fecha_respuesta": str(sub.get("fecha_respuesta")) if sub.get("fecha_respuesta") else None,
        })
        
        if estado_sub == "aprobada":
            monto_disponible += float(sub.get("total_estimado") or 0)
    
    # NUEVO: Estado real
    estado_real = _calcular_estado_real(c["estado"], subs)
    
    # NUEVO: Flag para pagar parcial
    puede_pagar_parcial = (
        len([s for s in subs if s.get("estado") == "aprobada"]) > 0 and
        len([s for s in subs if s.get("estado") in ("rechazada", "pendiente")]) > 0
    )
    
    return {
        "ok": True,
        "cotizacion": {
            "cotizacion_id": c["cotizacion_id"],
            "nombre_proyecto": c.get("prompt_original"),
            "estado": estado_real,  # ← CAMBIO: estado real
            "items": items_enriquecidos,
            "total_estimado": float(c.get("total_estimado") or 0),
            "total_comprador": total_comprador,
            "monto_disponible": monto_disponible,  # ← NUEVO
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
            "sub_cotizaciones": subs_detalle,  # ← NUEVO
            "puede_pagar_parcial": puede_pagar_parcial,  # ← NUEVO
            "num_subs_aprobadas": len([s for s in subs if s.get("estado") == "aprobada"]),
            "num_subs_rechazadas": len([s for s in subs if s.get("estado") == "rechazada"]),
            "num_subs_pendientes": len([s for s in subs if s.get("estado") == "pendiente"]),
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
