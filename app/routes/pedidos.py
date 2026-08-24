"""Flujo de aprobación y pago de cotizaciones.

Flujo de estados:
  borrador → enviada (comprador solicita)
           → aceptada (viverista aprueba)  → checkout → pagada
           → rechazada (viverista rechaza)

═══════════════════════════════════════════════════════════════════════════
Fase 4 RESTAURADA (6 ago 2026) — Modelo comercial matricial por categoría:

Antes: MARKUP_PLATAFORMA = 0.20 hardcoded
Ahora: Motor matricial en app/services/precios.py que calcula item-por-item
       según categoria_producto de cada SKU, respetando:
  - Regla del Productor v2: viverista siempre recibe su precio mayorista
  - Descuentos B2B ≥ 5 SMLMV: solo aplicables si compra >= umbral
  - Plazos 30/60/90d: solo si fintech_activa=true en configuracion_global

Todos los cálculos de precio_comprador se hacen con calcular_precios_pedido().

REGLA DE NEGOCIO — VISIBILIDAD DEL PRECIO COMPRADOR:
El viverista NUNCA debe ver cuánto paga el comprador. Solo ve SU precio
(el precio mayorista que él mismo publicó). Motivo: proteger el modelo
comercial. En este archivo:
  - Mensaje WhatsApp al viverista: solo "Tu precio"
  - Endpoint /pendientes: NO incluye total_comprador (solo total_estimado)
El comprador SÍ ve su total (en el mensaje de aprobación y en el checkout).

═══════════════════════════════════════════════════════════════════════════
FEATURE AUTO-TIMEOUT (6 ago 2026):
  - Cron vencer-cotizaciones extendido para procesar recordatorios + timeout
  - Endpoint POST /aceptar-parcial para cotizaciones parciales
  - Ver app/services/auto_timeout.py para la lógica completa
═══════════════════════════════════════════════════════════════════════════
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


def _resumir_items(db, items: list[dict]) -> str:
    """Genera resumen legible de los items para el mensaje WhatsApp."""
    if not items:
        return "Sin items"
    inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
    inv_map = {}
    if inv_ids:
        resp = db.table("inventario").select(
            "inventario_id, plantas(nombre_comun)"
        ).in_("inventario_id", inv_ids).execute()
        inv_map = {r["inventario_id"]: (r.get("plantas") or {}).get("nombre_comun", "Planta") 
                   for r in (resp.data or [])}
    
    lineas = []
    for it in items[:5]:
        nombre = inv_map.get(it.get("inventario_id"), "Planta")
        cantidad = it.get("cantidad", 0)
        lineas.append(f"  • {nombre} × {cantidad}")
    
    if len(items) > 5:
        lineas.append(f"  • ... y {len(items) - 5} más")
    
    return "\n".join(lineas)


# ═══════════ 1. COMPRADOR: Solicitar al vivero ═══════════

@router.post("/{cotizacion_id}/solicitar")
async def solicitar_aprobacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Comprador envía el borrador al viverista para aprobación.
    
    🔔 IMPORTANTE: Notifica al viverista por WhatsApp con resumen y link.
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
        base = get_settings().app_base_url
        items = cot.get("items") or []
        nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"
        notas = cot.get("notas_cliente", "")

        # Agrupar items por vivero
        inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
        if inv_ids:
            inv_resp = db.table("inventario").select(
                "inventario_id, vivero_id"
            ).in_("inventario_id", inv_ids).execute()
            inv_vivero_map = {r["inventario_id"]: r["vivero_id"] for r in (inv_resp.data or [])}

            # Agrupar items por vivero_id
            items_por_vivero: dict[int, list] = {}
            for it in items:
                vid = inv_vivero_map.get(it.get("inventario_id"))
                if vid:
                    items_por_vivero.setdefault(vid, []).append(it)

            # Crear sub-cotización y notificar a cada vivero
            for vivero_id, vitems in items_por_vivero.items():
                total_vivero = sum(float(it.get("subtotal") or 0) for it in vitems)

                # Crear sub-cotización
                subcot_resp = db.table("sub_cotizaciones").insert({
                    "cotizacion_id": cotizacion_id,
                    "vivero_id": vivero_id,
                    "items": vitems,
                    "total_estimado": total_vivero,
                    "estado": "pendiente",
                }).execute()

                # Tracking onboarding
                try:
                    marcar_primera_cotizacion(vivero_id)
                except Exception as e:
                    logger.warning(f"No se pudo marcar primera_cotizacion vivero {vivero_id}: {e}")

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # 🔔 NOTIFICAR AL VIVERISTA — LÍNEA CRÍTICA
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                v = db.table("viveros").select(
                    "whatsapp_numero, nombre_vivero"
                ).eq("vivero_id", vivero_id).limit(1).execute()

                if v.data and v.data[0].get("whatsapp_numero"):
                    viverista_wa = v.data[0]["whatsapp_numero"]
                    nombre_vivero = v.data[0].get("nombre_vivero", "Viverista")
                    resumen = _resumir_items(db, vitems)

                    # Mensaje al viverista (SIN mostrar precio comprador)
                    mensaje_viverista = (
                        f"📋 *Nueva cotización — ViveroOnline*\n\n"
                        f"Proyecto: *{nombre_proyecto}*\n"
                        f"Cotización: #{cotizacion_id}\n\n"
                        f"📦 *Items solicitados:*\n{resumen}\n\n"
                        f"💰 *Tu total (para aprobar):*\n"
                        f"${int(total_vivero):,} COP\n\n"
                        f"🔗 *Aprobá o rechazá aquí:*\n"
                        f"{base}/viverista\n\n"
                        f"⏰ Tienes hasta las 8:00 PM para responder."
                    )

                    try:
                        await send_text_message(viverista_wa, mensaje_viverista)
                        logger.info(f"✅ Notificación enviada a viverista {vivero_id} ({viverista_wa})")
                    except Exception as e:
                        logger.error(f"❌ Error enviando notificación a viverista {vivero_id}: {e}")
                        # NO bloquea el flujo si WhatsApp falla

    except Exception as e:
        logger.error(f"Error en solicitar_aprobacion: {e}")
        raise HTTPException(500, f"Error al procesar cotización: {str(e)}")

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
    """Viverista aprueba la sub-cotización."""
    if not user.vivero_id:
        raise HTTPException(403, "No estás registrado como viverista")

    db = db_admin()
    
    # Buscar sub-cotización
    sub_resp = db.table("sub_cotizaciones").select(
        "sub_cotizacion_id, vivero_id, estado, items, total_estimado"
    ).eq("cotizacion_id", cotizacion_id).eq("vivero_id", user.vivero_id).limit(1).execute()

    if not sub_resp.data:
        raise HTTPException(404, "Sub-cotización no encontrada")

    sub = sub_resp.data[0]
    if sub["estado"] != "pendiente":
        raise HTTPException(400, f"Solo sub-cotizaciones pendientes pueden ser aprobadas. Estado: {sub['estado']}")

    # Actualizar a aprobada
    db.table("sub_cotizaciones").update({
        "estado": "aprobada",
        "fecha_aprobacion": datetime.utcnow().isoformat(),
    }).eq("sub_cotizacion_id", sub["sub_cotizacion_id"]).execute()

    # Verificar si TODAS las sub-cotizaciones están aprobadas
    all_subs = db.table("sub_cotizaciones").select(
        "estado"
    ).eq("cotizacion_id", cotizacion_id).execute()

    todos_aprobados = all(s["estado"] in ("aprobada", "aprobada") for s in (all_subs.data or []))
    
    if todos_aprobados:
        # Actualizar cotización principal a aceptada
        db.table("cotizaciones").update({
            "estado": "aceptada",
            "fecha_aprobacion": datetime.utcnow().isoformat(),
        }).eq("cotizacion_id", cotizacion_id).execute()

    logger.info(f"✅ Viverista {user.vivero_id} aprobó sub-cotización #{sub['sub_cotizacion_id']}")

    return {
        "ok": True,
        "sub_cotizacion_id": sub["sub_cotizacion_id"],
        "estado": "aprobada",
        "total_viverista": sub["total_estimado"],
        "mensaje": "✅ Sub-cotización aprobada. Esperando aprobación de otros viveristas..." if not todos_aprobados else "✅ Todas las sub-cotizaciones aprobadas. El comprador puede pagar.",
    }


# ═══════════ 4. VIVERISTA: Rechazar sub-cotización ═══════════

@router.post("/{cotizacion_id}/rechazar-vivero")
async def rechazar_subcotizacion_vivero(
    cotizacion_id: int,
    motivo: str = "",
    user: UserContext = Depends(require_viverista),
):
    """Viverista rechaza la sub-cotización."""
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

    # Actualizar a rechazada
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
        "mensaje": "Sub-cotización rechazada. El comprador será notificado.",
    }


# ═══════════ 5. CRON — Vencer cotizaciones expiradas ═══════════

@router.get("/cron/vencer-cotizaciones")
async def vencer_cotizaciones_cron(request: Request):
    """Vence cotizaciones expiradas y notifica por WhatsApp a cada comprador."""
    import os
    cron_secret = os.getenv("CRON_SECRET", "")
    if cron_secret:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {cron_secret}":
            raise HTTPException(status_code=401, detail="No autorizado")

    db = db_admin()
    notificadas = 0
    errores_wa = 0

    try:
        result = db.rpc("vencer_cotizaciones_expiradas").execute()
        filas = result.data or []

        for fila in filas:
            cotizacion_id = fila.get("cotizacion_id")
            cliente_id = fila.get("cliente_id")
            if not cotizacion_id or not cliente_id:
                continue

            try:
                cot = db.table("cotizaciones").select(
                    "prompt_original"
                ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

                cliente = db.table("clientes").select(
                    "whatsapp_numero"
                ).eq("cliente_id", cliente_id).limit(1).execute()

                if not cliente.data or not cliente.data[0].get("whatsapp_numero"):
                    continue

                nombre_proyecto = (
                    (cot.data[0].get("prompt_original") if cot.data else None)
                    or f"Cotización #{cotizacion_id}"
                )
                base = get_settings().app_base_url

                msg = (
                    "⏰ *Tu cotización venció — ViveroOnline*\n\n"
                    f"Proyecto: *{nombre_proyecto}*\n\n"
                    "El tiempo para completar el pago expiró. "
                    "Podés volver a solicitar disponibilidad desde el marketplace:\n"
                    f"{base}/marketplace"
                )
                await send_text_message(cliente.data[0]["whatsapp_numero"], msg)
                notificadas += 1

            except Exception as e:
                errores_wa += 1
                logger.warning(f"No se pudo notificar vencimiento cotizacion_id={cotizacion_id}: {e}")

        # Procesar auto-timeout
        try:
            auto_timeout_stats = procesar_recordatorios_y_timeouts(dry_run=False)
            logger.info(f"Auto-timeout stats: {auto_timeout_stats}")
        except Exception as e:
            logger.exception(f"Error en procesar_recordatorios_y_timeouts: {e}")
            auto_timeout_stats = {"ok": False, "error": str(e)}

        return {
            "ok": True,
            "vencidas": len(filas),
            "notificadas": notificadas,
            "errores_wa": errores_wa,
            "auto_timeout": auto_timeout_stats,
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}
