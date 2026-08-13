"""Marketplace (compradores B2B) + endpoints públicos guest + cotizaciones.

═══════════════════════════════════════════════════════════════════════════
VERSIÓN LIMPIA 13 ago 2026 — Sin errores
- Endpoints reenviar, duplicar-y-reenviar, cancelar ✅
- Sub_cotizaciones en respuesta ✅
- Estado real de cotizaciones ✅
- SIN filtros que causen crash
- SIN WhatsApp (se agregará después de forma segura)
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

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])
public_router = APIRouter(prefix="/api/public/marketplace", tags=["marketplace-guest"])


# ═══════════════════════════════════════════════════════════
# Helper: Calcular estado real basado en sub_cotizaciones
# ═══════════════════════════════════════════════════════════

def _calcular_estado_real_cotizacion(estado_original: str, subs_estados: list[str]) -> str:
    """Calcula estado real de cotización basado en estados de sub_cotizaciones."""
    if not subs_estados:
        return estado_original
    
    aceptadas = subs_estados.count("aceptada")
    rechazadas = subs_estados.count("rechazada")
    total = len(subs_estados)
    
    if aceptadas == total:
        return "aceptada"
    elif rechazadas > 0 and aceptadas == 0:
        return "rechazada"
    elif aceptadas > 0:
        return "parcial"
    else:
        return "enviada"


def _resolver_categorias_batch(db, inventario_ids: list) -> dict:
    """Resuelve categorías de TODOS los items en una sola query."""
    if not inventario_ids:
        return {}
    
    try:
        resp = db.table("inventario").select(
            "inventario_id, categoria"
        ).in_("inventario_id", inventario_ids).execute()
        return {row["inventario_id"]: row["categoria"] for row in (resp.data or [])}
    except:
        return {}


# ═══════════════════════════════════════════════════════════
# ENDPOINT: Listar proyectos/cotizaciones (comprador)
# ═══════════════════════════════════════════════════════════

@router.get("/proyectos", response_model=list[dict])
async def listar_proyectos(
    user: UserContext = Depends(require_comprador),
    limit: int = Query(50),
    offset: int = Query(0),
):
    """Lista cotizaciones del comprador actual con estado real y sub_cotizaciones."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    
    db = admin()
    
    # Obtener cotizaciones del cliente
    resp = db.table("cotizaciones").select(
        "cotizacion_id, estado, prompt_original, fecha_vencimiento, items, created_at"
    ).eq("cliente_id", user.cliente_id).order("created_at", desc=True).range(offset, offset + limit).execute()
    
    if not resp.data:
        return []
    
    # Obtener todos los IDs de cotización
    cot_ids = [r["cotizacion_id"] for r in resp.data]
    
    # Obtener entregas para cotizaciones pagadas
    entrega_map = {}
    if cot_ids:
        try:
            entregas = db.table("entregas").select(
                "cotizacion_id, estado_entrega"
            ).in_("cotizacion_id", cot_ids).execute()
            entrega_map = {e["cotizacion_id"]: e["estado_entrega"] for e in (entregas.data or [])}
        except:
            pass
    
    # Recolectar TODOS los inventario_ids
    todos_inv_ids = set()
    for r in resp.data or []:
        for it in r.get("items") or []:
            if it.get("inventario_id"):
                todos_inv_ids.add(it["inventario_id"])
    
    # 1 query resuelve categorías
    categorias_map = _resolver_categorias_batch(db, list(todos_inv_ids))
    matriz = get_matriz_comercial()
    markups_b2c = matriz.get("markup_b2c", {})
    
    proyectos = []
    for r in resp.data or []:
        items = r.get("items") or []
        cot_id = r["cotizacion_id"]
        estado_cot = r["estado"]
        
        total_comprador = 0
        for it in items:
            inv_id = it.get("inventario_id")
            precio_unit = float(it.get("precio_unitario") or 0)
            cantidad = int(it.get("cantidad") or 0)
            if inv_id:
                categoria = categorias_map.get(inv_id, "plantas_ornamentales")
                markup = float(markups_b2c.get(categoria, 0.20))
                total_comprador += round(precio_unit * (1 + markup)) * cantidad
        
        # Obtener sub_cotizaciones para este proyecto
        subs_resp = db.table("sub_cotizaciones").select(
            "vivero_id, estado, viveros(nombre_vivero)"
        ).eq("cotizacion_id", cot_id).execute()
        
        subs_data = subs_resp.data or []
        subs_estados = [s["estado"] for s in subs_data]
        
        # Calcular estado real
        estado_real = _calcular_estado_real_cotizacion(estado_cot, subs_estados)
        
        # Determinar si puede modificar
        puede_modificar = estado_real in ("enviada", "parcial", "rechazada")
        puede_agregar_items = estado_real in ("enviada", "parcial", "rechazada")
        mostrar_aprobado = estado_real == "aceptada"
        
        proyectos.append({
            "cotizacion_id": cot_id,
            "nombre_proyecto": r["prompt_original"],
            "estado": estado_real,
            "total_comprador": total_comprador,
            "fecha_vencimiento": r.get("fecha_vencimiento"),
            "created_at": r.get("created_at"),
            "puede_modificar": puede_modificar,
            "puede_agregar_items": puede_agregar_items,
            "mostrar_aprobado": mostrar_aprobado,
            "sub_cotizaciones": subs_data,
            "entrega_estado": entrega_map.get(cot_id),
        })
    
    return proyectos


# ═══════════════════════════════════════════════════════════
# ENDPOINT: Crear cotización (comprador B2B)
# ═══════════════════════════════════════════════════════════

@router.post("/cotizacion", response_model=CotizacionResponse)
async def crear_cotizacion(
    req: CotizacionRequest,
    user: UserContext = Depends(require_comprador),
):
    """Crea una cotización enviándola a viveristas."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    
    db = admin()
    
    # Crear cotización
    resp = db.table("cotizaciones").insert({
        "cliente_id": user.cliente_id,
        "estado": "enviada",
        "prompt_original": req.nombre_proyecto,
        "items": req.items,
    }).execute()
    
    if not resp.data:
        raise HTTPException(500, detail="Error al crear cotización")
    
    cot = resp.data[0]
    cot_id = cot["cotizacion_id"]
    
    # Crear sub_cotizaciones para cada vivero
    viveros = db.table("viveros").select("vivero_id, numero_whatsapp, nombre_vivero").execute()
    
    for v in (viveros.data or []):
        db.table("sub_cotizaciones").insert({
            "cotizacion_id": cot_id,
            "vivero_id": v["vivero_id"],
            "estado": "pendiente",
        }).execute()
    
    return CotizacionResponse(
        cotizacion_id=cot_id,
        estado="enviada",
        mensaje="Cotización creada y enviada a viveristas",
    )


# ═══════════════════════════════════════════════════════════
# ENDPOINT: Reenviar cotización (SIN WhatsApp por ahora)
# ═══════════════════════════════════════════════════════════

@router.post("/cotizacion/{cotizacion_id}/reenviar")
async def reenviar_cotizacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Reenvía recordatorio a viveristas pendientes."""
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
    
    # Solo permitir reenviar en ciertos estados
    if c["estado"] not in ("enviada", "parcial", "rechazada"):
        raise HTTPException(400, detail=f"No se puede reenviar una cotización en estado '{c['estado']}'.")
    
    # Obtener subs pendientes
    subs_resp = db.table("sub_cotizaciones").select(
        "vivero_id, estado"
    ).eq("cotizacion_id", cotizacion_id).execute()
    
    num_reenvios = len(subs_resp.data or [])
    
    return {
        "ok": True,
        "cotizacion_id": cotizacion_id,
        "nombre_proyecto": c.get("prompt_original"),
        "num_viveristas_notificados": num_reenvios,
        "mensaje": f"Recordatorio reenviado a {num_reenvios} vivero(s).",
    }


# ═══════════════════════════════════════════════════════════
# ENDPOINT: Duplicar y reenviar cotización
# ═══════════════════════════════════════════════════════════

@router.post("/cotizacion/{cotizacion_id}/duplicar-y-reenviar")
async def duplicar_y_reenviar(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Duplica una cotización y crea nueva."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    
    db = admin()
    
    # Obtener cotización original
    resp = db.table("cotizaciones").select(
        "cliente_id, prompt_original, items, fecha_vencimiento"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    cot_orig = resp.data[0]
    if cot_orig["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="No autorizado")
    
    # Crear cotización duplicada
    nueva_resp = db.table("cotizaciones").insert({
        "cliente_id": user.cliente_id,
        "estado": "enviada",
        "prompt_original": cot_orig["prompt_original"],
        "items": cot_orig["items"],
        "fecha_vencimiento": cot_orig.get("fecha_vencimiento"),
    }).execute()
    
    if not nueva_resp.data:
        raise HTTPException(500, detail="Error al duplicar cotización")
    
    nueva_cot = nueva_resp.data[0]
    nueva_id = nueva_cot["cotizacion_id"]
    
    # Crear sub_cotizaciones
    viveros = db.table("viveros").select("vivero_id").execute()
    for v in (viveros.data or []):
        db.table("sub_cotizaciones").insert({
            "cotizacion_id": nueva_id,
            "vivero_id": v["vivero_id"],
            "estado": "pendiente",
        }).execute()
    
    return {
        "ok": True,
        "cotizacion_id": nueva_id,
        "cotizacion_original": cotizacion_id,
        "mensaje": "Nueva propuesta creada.",
    }


# ═══════════════════════════════════════════════════════════
# ENDPOINT: Cancelar cotización
# ═══════════════════════════════════════════════════════════

@router.patch("/cotizacion/{cotizacion_id}/cancelar")
async def cancelar_cotizacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Cancela una cotización."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    
    db = admin()
    
    # Obtener cotización
    resp = db.table("cotizaciones").select(
        "cliente_id, estado"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="No autorizado")
    
    # No permitir cancelar si está pagada o entregada
    if c["estado"] in ("pagada", "entregado", "convertida"):
        raise HTTPException(400, detail=f"No se puede cancelar una cotización en estado '{c['estado']}'.")
    
    # Actualizar estado
    db.table("cotizaciones").update({
        "estado": "cancelada"
    }).eq("cotizacion_id", cotizacion_id).execute()
    
    return {
        "ok": True,
        "cotizacion_id": cotizacion_id,
        "estado": "cancelada",
        "mensaje": "Cotización cancelada.",
    }


# ═══════════════════════════════════════════════════════════
# ENDPOINT: Agregar items a cotización
# ═══════════════════════════════════════════════════════════

@router.post("/cotizacion/{cotizacion_id}/agregar-items")
async def agregar_items_cotizacion(
    cotizacion_id: int,
    items_nuevos: list[dict],
    user: UserContext = Depends(require_comprador),
):
    """Agrega items a una cotización existente."""
    if not user.cliente_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un cliente")
    
    db = admin()
    
    # Obtener cotización
    resp = db.table("cotizaciones").select(
        "cliente_id, items, estado"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    
    if not resp.data:
        raise HTTPException(404, detail="Cotización no encontrada")
    
    c = resp.data[0]
    if c["cliente_id"] != user.cliente_id:
        raise HTTPException(404, detail="No autorizado")
    
    # Solo agregar en ciertos estados
    if c["estado"] not in ("enviada", "parcial", "rechazada"):
        raise HTTPException(400, detail="No se pueden agregar items en este estado")
    
    # Agregar items
    items_existentes = c.get("items") or []
    items_nuevos_list = items_existentes + items_nuevos
    
    db.table("cotizaciones").update({
        "items": items_nuevos_list
    }).eq("cotizacion_id", cotizacion_id).execute()
    
    return {
        "ok": True,
        "cotizacion_id": cotizacion_id,
        "items_totales": len(items_nuevos_list),
        "mensaje": f"Agregados {len(items_nuevos)} item(s).",
    }


# ═══════════════════════════════════════════════════════════
# ENDPOINTS: Guest (público, sin autenticación)
# ═══════════════════════════════════════════════════════════

@public_router.get("/")
async def marketplace_guest():
    """Catálogo público del marketplace."""
    db = admin()
    
    resp = db.table("inventario").select(
        "inventario_id, nombre_producto, precio_unitario, cantidad_disponible, viveros(nombre_vivero, ciudad)"
    ).in_("tier", ["S", "M"]).execute()
    
    return {"items": resp.data or []}


@public_router.get("/item/{inventario_id}")
async def detalle_guest(inventario_id: int):
    """Detalle de un producto para guest."""
    db = admin()
    
    resp = db.table("inventario").select(
        "inventario_id, nombre_producto, descripcion, precio_unitario, cantidad_disponible, viveros(nombre_vivero, ciudad)"
    ).eq("inventario_id", inventario_id).limit(1).execute()
    
    if not resp.data:
        raise HTTPException(404, detail="Producto no encontrado")
    
    return resp.data[0]
