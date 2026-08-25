"""Flujo de aprobación y pago de cotizaciones — V2 (19 ago 2026).

FLUJO CORREGIDO:
  borrador → enviada (comprador solicita) → sub-cotización #1
           → enviada (comprador agrega) → sub-cotización #2
           → aceptada (TODAS las sub-cotizaciones aprobadas)
           → checkout → pagada → mensaje final "confirmado X + Y = $Z"

Cada MENSAJE es una SUB-COTIZACIÓN INDEPENDIENTE.
Cada sub-cotización REQUIERE respuesta del viverista (APROBAR/RECHAZAR).
En PAGO se suman SOLO las aprobadas.
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from app.auth.deps import UserContext, require_comprador, require_viverista
from app.config import get_settings
from app.services.supabase import admin as db_admin
from app.services.whatsapp_meta import send_text_message
from app.services.onboarding_wa import marcar_primera_cotizacion
from app.services.precios import calcular_precios_pedido
from app.services.auto_timeout import procesar_recordatorios_y_timeouts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pedidos", tags=["pedidos"])


def _obtener_cliente(db, cliente_id: int) -> dict:
    """Lee los datos del cliente necesarios para el motor de precios."""
    resp = db.table("clientes").select(
        "cliente_id, es_guest, tipo_cliente, activo, nombre_empresa, "
        "nombre_representante, whatsapp_numero"
    ).eq("cliente_id", cliente_id).limit(1).execute()
    return resp.data[0] if resp.data else {"cliente_id": cliente_id, "es_guest": False}


def _calcular_para_cotizacion(
    db,
    cliente_id: int,
    items: list[dict],
    plazo: str = "inmediato",
) -> dict:
    """Wrapper que resuelve el cliente y llama al motor matricial."""
    cliente = _obtener_cliente(db, cliente_id)
    return calcular_precios_pedido(
        cliente=cliente,
        items=items or [],
        plazo=plazo,
    )


def _get_cotizacion(db, cotizacion_id: int) -> dict:
    r = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, estado, items, total_estimado, "
        "prompt_original, notas_cliente, ciudad_entrega, fecha_entrega_deseada"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    if not r.data:
        raise HTTPException(404, "Cotización no encontrada")
    return r.data[0]


# ═══════════ 1. COMPRADOR: Solicitar al vivero ═══════════

@router.post("/{cotizacion_id}/solicitar")
async def solicitar_aprobacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Comprador envía el borrador al viverista para aprobación.
    
    V2 (19 ago 2026): Cada mensaje es UNA SUB-COTIZACIÓN.
    El viverista DEBE responder APROBAR o RECHAZAR.
    """
    db = db_admin()
    cot = _get_cotizacion(db, cotizacion_id)

    if cot["cliente_id"] != user.cliente_id:
        raise HTTPException(403, "No podés solicitar esta cotización")
    if cot["estado"] != "borrador":
        raise HTTPException(400, f"Solo los borradores se pueden enviar. Estado actual: {cot['estado']}")
    if not cot.get("items"):
        raise HTTPException(400, "El carrito está vacío")

    db.table("cotizaciones").update({"estado": "enviada"}).eq("cotizacion_id", cotizacion_id).execute()

    # ── Crear sub-cotizaciones por vivero y notificar ─────────────────────────
    try:
        items = cot.get("items") or []
        nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"
        ciudad_entrega = cot.get("ciudad_entrega", "Sabana de Bogotá")
        
        # PASO 1: Obtener precio_mayorista de CADA inventario
        inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
        
        if not inv_ids:
            raise HTTPException(400, "No hay items con inventario_id")
        
        inv_resp = db.table("inventario").select(
            "inventario_id, vivero_id, precio_mayorista, plantas(nombre_comun)"
        ).in_("inventario_id", inv_ids).execute()
        
        if not inv_resp.data:
            raise HTTPException(400, "No se encontraron inventarios")
        
        # Crear mapas
        inv_vivero_map = {}
        inv_precio_mayorista_map = {}
        inv_planta_map = {}
        
        for r in inv_resp.data:
            inv_id = r["inventario_id"]
            inv_vivero_map[inv_id] = r["vivero_id"]
            inv_precio_mayorista_map[inv_id] = float(r.get("precio_mayorista") or 0)
            planta = (r.get("plantas") or {}).get("nombre_comun", "Planta")
            inv_planta_map[inv_id] = planta
            logger.info(f"  inv{inv_id}: {planta} (vivero {r['vivero_id']}) → ${inv_precio_mayorista_map[inv_id]:,.0f}")
        
        # PASO 2: Agrupar items por vivero
        items_por_vivero: dict[int, list] = {}
        for it in items:
            inv_id = it.get("inventario_id")
            vid = inv_vivero_map.get(inv_id)
            if vid:
                items_por_vivero.setdefault(vid, []).append(it)
        
        if not items_por_vivero:
            raise HTTPException(400, "No hay items válidos para procesar")
        
        # PASO 3: Para CADA VIVERO, crear sub-cotización y notificar
        for vivero_id, vitems in items_por_vivero.items():
            logger.info(f"\n🌱 Procesando vivero {vivero_id}:")
            
            # Calcular precio mayorista TOTAL para este vivero
            total_mayorista_vivero = 0.0
            plantas_detalle = []
            
            for it in vitems:
                inv_id = it.get("inventario_id")
                cantidad = it.get("cantidad", 0)
                precio_unit = inv_precio_mayorista_map.get(inv_id, 0)
                subtotal_item = precio_unit * cantidad
                total_mayorista_vivero += subtotal_item
                
                nombre_planta = inv_planta_map.get(inv_id, "Planta")
                plantas_detalle.append({
                    "nombre": nombre_planta,
                    "cantidad": cantidad,
                    "precio_unitario": precio_unit,
                    "subtotal": subtotal_item
                })
            
            logger.info(f"  Total mayorista vivero {vivero_id}: ${total_mayorista_vivero:,.0f}")
            
            # ─────────────────────────────────────────────────────────────────
            # CREAR SUB-COTIZACIÓN (V2: INDEPENDIENTE para este mensaje)
            # ─────────────────────────────────────────────────────────────────
            db.table("sub_cotizaciones").insert({
                "cotizacion_id": cotizacion_id,
                "vivero_id": vivero_id,
                "items": vitems,
                "total_estimado": total_mayorista_vivero,
                "estado": "pendiente",
            }).execute()
            
            # Tracking onboarding
            try:
                marcar_primera_cotizacion(vivero_id)
            except Exception as e:
                logger.warning(f"No se pudo marcar primera_cotizacion vivero {vivero_id}: {e}")
            
            # ─────────────────────────────────────────────────────────────────
            # NOTIFICAR AL VIVERISTA (Mensaje 1: Nueva solicitud)
            # ─────────────────────────────────────────────────────────────────
            v = db.table("viveros").select(
                "whatsapp_numero, nombre_vivero"
            ).eq("vivero_id", vivero_id).limit(1).execute()
            
            if not v.data or not v.data[0].get("whatsapp_numero"):
                logger.warning(f"  ⚠️ Vivero {vivero_id} sin WhatsApp")
                continue
            
            viverista_wa = v.data[0]["whatsapp_numero"]
            nombre_vivero = v.data[0].get("nombre_vivero", "Viverista")
            
            # Construir línea de plantas
            lineas_plantas = []
            for planta in plantas_detalle:
                linea = f"  • {planta['nombre']} × {planta['cantidad']} → ${planta['precio_unitario']:,.0f} COP"
                lineas_plantas.append(linea)
            
            plantas_msg = "\n".join(lineas_plantas)
            
            # Mensaje: Nueva solicitud (MENSAJE 1)
            mensaje_viverista = (
                f"🌿 *Nueva solicitud — ViveroOnline*\n\n"
                f"Proyecto: *{nombre_proyecto}*\n"
                f"Solicitante: *Cliente ViveroOnline*\n\n"
                f"📦 *Plantas solicitadas:*\n{plantas_msg}\n\n"
                f"💰 *Tu precio total (lo que recibirás):* ${total_mayorista_vivero:,.0f} COP\n\n"
                f"Zona: {ciudad_entrega}\n"
                f"⏰ Responde en 2h\n\n"
                f"¿Confirmás disponibilidad?\n"
                f"Respondé *APROBAR* o *RECHAZAR*"
            )
            
            try:
                resultado = await send_text_message(viverista_wa, mensaje_viverista)
                if resultado:
                    logger.info(f"✅ Mensaje 1 (Nueva solicitud) enviado a {nombre_vivero}")
                else:
                    logger.error(f"❌ send_text_message devolvió False para {viverista_wa}")
            except Exception as e:
                logger.error(f"❌ Exception enviando WhatsApp a vivero {vivero_id}: {e}", exc_info=True)
            
            # Guardar acción pendiente en sesión
            try:
                accion = {
                    "type": "aprobar_rechazar_cotizacion",
                    "confirmed": False,
                    "params": {
                        "cotizacion_id": cotizacion_id,
                        "nombre_proyecto": nombre_proyecto,
                        "total_base": int(total_mayorista_vivero),
                    }
                }
                sesion = db.table("sesiones_agente").select(
                    "sesion_id"
                ).eq("whatsapp_numero", viverista_wa).eq(
                    "estado", "activa"
                ).limit(1).execute()
                
                if sesion.data:
                    db.table("sesiones_agente").update({
                        "accion_pendiente": accion
                    }).eq("sesion_id", sesion.data[0]["sesion_id"]).execute()
            except Exception as e:
                logger.warning(f"No se pudo guardar acción pendiente: {e}")
    
    except Exception as e:
        logger.error(f"❌ Error en solicitar_aprobacion: {e}", exc_info=True)
        raise HTTPException(500, f"Error al procesar cotización: {str(e)[:200]}")
    
    return {
        "ok": True,
        "cotizacion_id": cotizacion_id,
        "estado": "enviada",
        "mensaje": "✅ Cotización enviada a los viveristas. Esperando respuestas...",
    }


# ═══════════ 2. VIVERISTA: Obtener cotizaciones pendientes ═══════════

@router.get("/pendientes")
async def cotizaciones_pendientes(user: UserContext = Depends(require_viverista)):
    """Lista cotizaciones pendientes de aprobación para este viverista."""
    if not user.vivero_id:
        raise HTTPException(403, "No estás registrado como viverista")
    db = db_admin()
    subs = db.table("sub_cotizaciones").select(
        "sub_cotizacion_id, cotizacion_id, items, total_estimado, estado, fecha_creacion"
    ).eq("vivero_id", user.vivero_id).eq("estado", "pendiente").order(
        "fecha_creacion", desc=True
    ).execute()
    result = []
    for sub in (subs.data or []):
        cot = db.table("cotizaciones").select(
            "prompt_original"
        ).eq("cotizacion_id", sub["cotizacion_id"]).limit(1).execute()
        
        nombre_proyecto = "Sin nombre"
        if cot.data:
            nombre_proyecto = cot.data[0].get("prompt_original", "Sin nombre")
        result.append({
            "sub_cotizacion_id": sub["sub_cotizacion_id"],
            "cotizacion_id": sub["cotizacion_id"],
            "proyecto": nombre_proyecto,
            "total_viverista": sub["total_estimado"],
            "estado": sub["estado"],
            "fecha_creacion": sub["fecha_creacion"],
            "cantidad_items": len(sub.get("items") or []),
        })
    return {"ok": True, "pendientes": result}


# ═══════════ 3. VIVERISTA: Aprobar sub-cotización ═══════════

@router.post("/{cotizacion_id}/aprobar-vivero")
async def aprobar_subcotizacion_vivero(
    cotizacion_id: int,
    user: UserContext = Depends(require_viverista),
):
    """Viverista aprueba la sub-cotización (RESPONDE A UN MENSAJE)."""
    if not user.vivero_id:
        raise HTTPException(403, "No estás registrado como viverista")
    db = db_admin()
    
    sub_resp = db.table("sub_cotizaciones").select(
        "sub_cotizacion_id, vivero_id, estado, items, total_estimado"
    ).eq("cotizacion_id", cotizacion_id).eq("vivero_id", user.vivero_id).limit(1).execute()
    if not sub_resp.data:
        raise HTTPException(404, "Sub-cotización no encontrada")
    sub = sub_resp.data[0]
    if sub["estado"] != "pendiente":
        raise HTTPException(400, f"Solo sub-cotizaciones pendientes pueden ser aprobadas. Estado: {sub['estado']}")
    
    db.table("sub_cotizaciones").update({
        "estado": "aprobada",
        "fecha_aprobacion": datetime.utcnow().isoformat(),
    }).eq("sub_cotizacion_id", sub["sub_cotizacion_id"]).execute()
    
    logger.info(f"✅ Viverista {user.vivero_id} aprobó sub-cotización #{sub['sub_cotizacion_id']}")
    
    return {
        "ok": True,
        "sub_cotizacion_id": sub["sub_cotizacion_id"],
        "estado": "aprobada",
        "total_viverista": sub["total_estimado"],
    }


# ═══════════ 4. VIVERISTA: Rechazar sub-cotización ═══════════

@router.post("/{cotizacion_id}/rechazar-vivero")
async def rechazar_subcotizacion_vivero(
    cotizacion_id: int,
    motivo: str = "",
    user: UserContext = Depends(require_viverista),
):
    """Viverista rechaza la sub-cotización (RESPONDE A UN MENSAJE)."""
    if not user.vivero_id:
        raise HTTPException(403, "No estás registrado como viverista")
    db = db_admin()
    
    sub_resp = db.table("sub_cotizaciones").select(
        "sub_cotizacion_id, vivero_id, estado"
    ).eq("cotizacion_id", cotizacion_id).eq("vivero_id", user.vivero_id).limit(1).execute()
    if not sub_resp.data:
        raise HTTPException(404, "Sub-cotización no encontrada")
    sub = sub_resp.data[0]
    if sub["estado"] != "pendiente":
        raise HTTPException(400, f"Solo sub-cotizaciones pendientes pueden ser rechazadas. Estado: {sub['estado']}")
    
    db.table("sub_cotizaciones").update({
        "estado": "rechazada",
        "fecha_rechazo": datetime.utcnow().isoformat(),
        "motivo_rechazo": motivo or "No especificado",
    }).eq("sub_cotizacion_id", sub["sub_cotizacion_id"]).execute()
    
    logger.info(f"❌ Viverista {user.vivero_id} rechazó sub-cotización #{sub['sub_cotizacion_id']}")
    
    return {
        "ok": True,
        "sub_cotizacion_id": sub["sub_cotizacion_id"],
        "estado": "rechazada",
    }


# ═══════════ 5. CRON — Vencer cotizaciones expiradas ═══════════

@router.get("/cron/vencer-cotizaciones")
async def vencer_cotizaciones_cron(request: Request):
    """Vence cotizaciones expiradas y procesa auto-timeout."""
    import os
    cron_secret = os.getenv("CRON_SECRET", "")
    if cron_secret:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {cron_secret}":
            raise HTTPException(status_code=401, detail="No autorizado")
    db = db_admin()
    try:
        result = db.rpc("vencer_cotizaciones_expiradas").execute()
        filas = result.data or []
        try:
            auto_timeout_stats = procesar_recordatorios_y_timeouts(dry_run=False)
            logger.info(f"Auto-timeout stats: {auto_timeout_stats}")
        except Exception as e:
            logger.exception(f"Error en procesar_recordatorios_y_timeouts: {e}")
            auto_timeout_stats = {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "vencidas": len(filas),
            "auto_timeout": auto_timeout_stats,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
