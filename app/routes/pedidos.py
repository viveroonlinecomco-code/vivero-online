"""Flujo de aprobación y pago de cotizaciones.

Flujo de estados:
  borrador → enviada (comprador solicita)
           → aceptada (viverista aprueba)  → checkout → pagada
           → rechazada (viverista rechaza)

Modelo de precios:
  - precio_mayorista en BD = precio BASE del viverista (lo que él recibe)
  - El comprador paga: total_cotizacion × 1.18 (18% de markup de plataforma) + flete
  - ViveroOnline retiene: total_cotizacion × 0.18
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.auth.deps import UserContext, require_comprador, require_viverista
from app.config import get_settings
from app.services.supabase import admin as db_admin
from app.services.whatsapp_meta import send_text_message

router = APIRouter(prefix="/api/pedidos", tags=["pedidos"])

MARKUP_PLATAFORMA = 0.18


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
    for it in items[:5]:  # máximo 5 items en el mensaje
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
    """Comprador envía el borrador al viverista para aprobación."""
    db = db_admin()
    cot = _get_cotizacion(db, cotizacion_id)

    if cot["cliente_id"] != user.cliente_id:
        raise HTTPException(403, "No podés solicitar esta cotización")
    if cot["estado"] != "borrador":
        raise HTTPException(400, f"Solo los borradores se pueden enviar. Estado actual: {cot['estado']}")
    if not cot.get("items"):
        raise HTTPException(400, "El carrito está vacío")

    db.table("cotizaciones").update({"estado": "enviada"}).eq("cotizacion_id", cotizacion_id).execute()

    # ── Notificar al viverista por WhatsApp ──────────────────────────────────
    try:
        base = get_settings().app_base_url
        items = cot.get("items") or []
        inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]

        if inv_ids:
            inv_resp = db.table("inventario").select("vivero_id").in_(
                "inventario_id", inv_ids
            ).execute()
            vivero_ids = list({r["vivero_id"] for r in (inv_resp.data or []) if r.get("vivero_id")})

            if len(vivero_ids) == 1:
                v = db.table("viveros").select(
                    "whatsapp_numero, nombre_vivero"
                ).eq("vivero_id", vivero_ids[0]).limit(1).execute()

                if v.data and v.data[0].get("whatsapp_numero"):
                    total_base = int(float(cot.get("total_estimado") or 0))
                    total_comprador = round(total_base * (1 + MARKUP_PLATAFORMA))
                    nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"
                    resumen = _resumir_items(db, items)
                    notas = cot.get("notas_cliente", "")

                    msg = (
                        f"🌿 *Nueva solicitud — ViveroOnline*\n\n"
                        f"Proyecto: *{nombre_proyecto}*\n\n"
                        f"📦 *Plantas solicitadas:*\n{resumen}\n\n"
                        f"💰 Tu precio: ${total_base:,} COP\n"
                        f"🛒 Comprador paga: ${total_comprador:,} COP\n"
                    )
                    if notas:
                        msg += f"\n📝 Notas: {notas}\n"
                    msg += (
                        f"\n¿Confirmás disponibilidad?\n"
                        f"Respondé *APROBAR* o *RECHAZAR*"
                    )
                    await send_text_message(v.data[0]["whatsapp_numero"], msg)

                    # Guardar vivero_id en sesión para que el bot sepa qué cotización aprobar
                    sesion = db.table("sesiones_agente").select(
                        "sesion_id, accion_pendiente"
                    ).eq("whatsapp_numero", v.data[0]["whatsapp_numero"]).eq(
                        "estado", "activa"
                    ).limit(1).execute()

                    accion = {
                        "type": "aprobar_rechazar_cotizacion",
                        "confirmed": False,
                        "params": {
                            "cotizacion_id": cotizacion_id,
                            "nombre_proyecto": nombre_proyecto,
                            "total_base": total_base,
                        }
                    }
                    if sesion.data:
                        db.table("sesiones_agente").update({
                            "accion_pendiente": accion
                        }).eq("sesion_id", sesion.data[0]["sesion_id"]).execute()
                    else:
                        # Crear sesión para el viverista si no existe
                        perfil = db.table("perfiles").select(
                            "id, vivero_id"
                        ).eq("whatsapp_numero", v.data[0]["whatsapp_numero"]).eq(
                            "rol", "viverista"
                        ).limit(1).execute()
                        if perfil.data:
                            db.table("sesiones_agente").insert({
                                "whatsapp_numero": v.data[0]["whatsapp_numero"],
                                "tipo_usuario": "viverista",
                                "vivero_id": vivero_ids[0],
                                "estado": "activa",
                                "flujo_actual": "chat",
                                "contexto_json": {"historial": []},
                                "mensajes_count": 0,
                                "fotos_procesadas": 0,
                                "accion_pendiente": accion,
                            }).execute()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"No se pudo notificar al viverista: {e}")

    return {"ok": True, "estado": "enviada", "cotizacion_id": cotizacion_id}


# ═══════════ 2. VIVERISTA: Ver cotizaciones pendientes ═══════════

@router.get("/pendientes")
async def listar_pendientes(user: UserContext = Depends(require_viverista)):
    db = db_admin()
    if not user.vivero_id:
        raise HTTPException(400, "Tu perfil no está vinculado a un vivero")

    r = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, estado, items, total_estimado, "
        "prompt_original, notas_cliente, fecha_creacion"
    ).eq("estado", "enviada").execute()

    pendientes = []
    for cot in (r.data or []):
        items = cot.get("items") or []
        if not items:
            continue

        inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
        if not inv_ids:
            continue

        inv_resp = db.table("inventario").select(
            "inventario_id, vivero_id, plantas(nombre_comun)"
        ).in_("inventario_id", inv_ids).execute()
        inv_map = {row["inventario_id"]: row for row in (inv_resp.data or [])}

        mis_items = []
        for it in items:
            inv = inv_map.get(it.get("inventario_id"))
            if not inv or inv.get("vivero_id") != user.vivero_id:
                continue
            planta = inv.get("plantas") or {}
            mis_items.append({
                **it,
                "vivero_id": user.vivero_id,
                "nombre_comun": planta.get("nombre_comun") or f"Item #{it.get('inventario_id')}",
            })

        if not mis_items:
            continue

        cliente = db.table("clientes").select(
            "nombre_empresa, nombre_representante"
        ).eq("cliente_id", cot["cliente_id"]).limit(1).execute()
        nombre_comprador = "Comprador"
        if cliente.data:
            nombre_comprador = (
                cliente.data[0].get("nombre_representante") or
                cliente.data[0].get("nombre_empresa") or
                "Comprador"
            )

        total_base = float(cot.get("total_estimado") or 0)
        pendientes.append({
            "cotizacion_id": cot["cotizacion_id"],
            "nombre_proyecto": cot.get("prompt_original") or f"Cotización #{cot['cotizacion_id']}",
            "nombre_comprador": nombre_comprador,
            "estado": cot["estado"],
            "items": mis_items,
            "total_estimado": total_base,
            "total_comprador": round(total_base * (1 + MARKUP_PLATAFORMA)),
            "notas_cliente": cot.get("notas_cliente"),
            "fecha_creacion": str(cot.get("fecha_creacion") or ""),
        })

    return {"ok": True, "pendientes": pendientes, "total": len(pendientes)}


# ═══════════ 3. VIVERISTA: Aprobar cotización ═══════════

class RechazarReq(BaseModel):
    motivo: Optional[str] = None


@router.post("/{cotizacion_id}/aprobar")
async def aprobar_cotizacion(cotizacion_id: int, user: UserContext = Depends(require_viverista)):
    db = db_admin()
    cot = _get_cotizacion(db, cotizacion_id)

    if cot["estado"] != "enviada":
        raise HTTPException(400, f"Solo se pueden aprobar cotizaciones enviadas. Estado: {cot['estado']}")

    items = cot.get("items") or []
    inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
    if inv_ids:
        inv_resp = db.table("inventario").select("inventario_id, vivero_id").in_(
            "inventario_id", inv_ids
        ).execute()
        if not any(r.get("vivero_id") == user.vivero_id for r in (inv_resp.data or [])):
            raise HTTPException(403, "Esta cotización no contiene items de tu vivero")

    db.table("cotizaciones").update({"estado": "aceptada"}).eq("cotizacion_id", cotizacion_id).execute()

    # ── Notificar al comprador por WhatsApp ──────────────────────────────────
    try:
        base = get_settings().app_base_url
        cliente = db.table("clientes").select("whatsapp_numero").eq(
            "cliente_id", cot["cliente_id"]
        ).limit(1).execute()

        if cliente.data and cliente.data[0].get("whatsapp_numero"):
            total_base = int(float(cot.get("total_estimado") or 0))
            total_comprador = round(total_base * (1 + MARKUP_PLATAFORMA))
            nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"
            msg = (
                f"✅ *¡Tu solicitud fue aprobada! — ViveroOnline*\n\n"
                f"Proyecto: *{nombre_proyecto}*\n"
                f"Total a pagar: *${total_comprador:,} COP*\n\n"
                f"El vivero confirmó disponibilidad.\n"
                f"Ingresá a tu panel para completar el pago:\n"
                f"{base}/comprador"
            )
            await send_text_message(cliente.data[0]["whatsapp_numero"], msg)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"No se pudo notificar al comprador: {e}")

    return {"ok": True, "estado": "aceptada", "cotizacion_id": cotizacion_id}


# ═══════════ 4. VIVERISTA: Rechazar cotización ═══════════

@router.post("/{cotizacion_id}/rechazar")
async def rechazar_cotizacion(
    cotizacion_id: int, req: RechazarReq, user: UserContext = Depends(require_viverista)
):
    db = db_admin()
    cot = _get_cotizacion(db, cotizacion_id)

    if cot["estado"] != "enviada":
        raise HTTPException(400, f"Solo se pueden rechazar cotizaciones enviadas. Estado: {cot['estado']}")

    items = cot.get("items") or []
    inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
    if inv_ids:
        inv_resp = db.table("inventario").select("inventario_id, vivero_id").in_(
            "inventario_id", inv_ids
        ).execute()
        if not any(r.get("vivero_id") == user.vivero_id for r in (inv_resp.data or [])):
            raise HTTPException(403, "Esta cotización no contiene items de tu vivero")

    db.table("cotizaciones").update({
        "estado": "rechazada",
        "notas_agente": req.motivo or "Rechazada por el viverista",
    }).eq("cotizacion_id", cotizacion_id).execute()

    # ── Notificar al comprador ────────────────────────────────────────────────
    try:
        base = get_settings().app_base_url
        cliente = db.table("clientes").select("whatsapp_numero").eq(
            "cliente_id", cot["cliente_id"]
        ).limit(1).execute()

        if cliente.data and cliente.data[0].get("whatsapp_numero"):
            nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"
            motivo_txt = f"\nMotivo: {req.motivo}" if req.motivo else ""
            msg = (
                f"❌ *Solicitud no disponible — ViveroOnline*\n\n"
                f"Proyecto: {nombre_proyecto}{motivo_txt}\n\n"
                f"El vivero no tiene disponibilidad en este momento.\n"
                f"Podés buscar alternativas en el marketplace:\n"
                f"{base}/marketplace"
            )
            await send_text_message(cliente.data[0]["whatsapp_numero"], msg)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"No se pudo notificar al comprador: {e}")

    return {"ok": True, "estado": "rechazada", "cotizacion_id": cotizacion_id}


# ═══════════ 5. COMPRADOR: Calcular flete antes del pago ═══════════

@router.get("/{cotizacion_id}/calcular-flete")
async def calcular_flete_cotizacion(
    cotizacion_id: int,
    ciudad: str,
    user: UserContext = Depends(require_comprador),
):
    db = db_admin()

    cot = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, estado"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

    if not cot.data:
        raise HTTPException(404, "Cotización no encontrada")
    if cot.data[0]["cliente_id"] != user.cliente_id:
        raise HTTPException(403, "No podés ver esta cotización")

    FALLBACK = {
        "ok": True, "tier": "M", "zona": "sabana_entre_municipios",
        "precio_base": 65000, "fee_carga_viva": 6500, "total_flete": 71500, "detalle": [],
    }

    try:
        result = db_admin().rpc("calcular_flete", {
            "p_cotizacion_id": cotizacion_id,
            "p_ciudad_destino": ciudad,
        }).execute()

        if not result.data:
            return FALLBACK

        r = result.data[0]
        return {
            "ok":             True,
            "tier":           r.get("tier_calculado", "M"),
            "zona":           r.get("zona_calculada", ""),
            "precio_base":    r.get("precio_base_cop", 0),
            "fee_carga_viva": r.get("fee_carga_viva_cop", 0),
            "total_flete":    r.get("total_flete_cop", 0),
            "detalle":        r.get("detalle", []),
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"Error calculando flete: {e}")
        return FALLBACK


# ═══════════ 6. COMPRADOR: Iniciar pago ═══════════

class CheckoutReq(BaseModel):
    ciudad_entrega:           Optional[str] = None
    fecha_entrega_deseada:    Optional[str] = None
    notas:                    Optional[str] = None
    direccion_entrega_exacta: Optional[str] = None
    contacto_nombre:          Optional[str] = None
    contacto_telefono:        Optional[str] = None
    tipo_vehiculo:            Optional[str] = None
    ventana_inicio:           Optional[str] = None
    ventana_fin:              Optional[str] = None
    flete_cop:                Optional[int] = None


@router.post("/{cotizacion_id}/checkout")
async def iniciar_checkout(
    cotizacion_id: int, req: CheckoutReq, user: UserContext = Depends(require_comprador)
):
    from app.services.epayco import get_epayco, CheckoutRequest

    db = db_admin()
    cot = _get_cotizacion(db, cotizacion_id)

    if cot["cliente_id"] != user.cliente_id:
        raise HTTPException(403, "No podés pagar esta cotización")

    if cot["estado"] not in ("aceptada", "convertida"):
        raise HTTPException(400, f"Solo se pueden pagar cotizaciones aceptadas. Estado: {cot['estado']}")

    s = get_settings()
    epayco = get_epayco()
    if not epayco.is_configured:
        raise HTTPException(503, "Servicio de pagos no configurado")

    update_data: dict = {}
    if req.ciudad_entrega:           update_data["ciudad_entrega"]           = req.ciudad_entrega
    if req.fecha_entrega_deseada:    update_data["fecha_entrega_deseada"]    = req.fecha_entrega_deseada
    if req.notas:                    update_data["notas_cliente"]             = req.notas
    if req.direccion_entrega_exacta: update_data["direccion_entrega_exacta"] = req.direccion_entrega_exacta
    if req.contacto_nombre:          update_data["contacto_nombre"]           = req.contacto_nombre
    if req.contacto_telefono:        update_data["contacto_telefono"]         = req.contacto_telefono
    if req.tipo_vehiculo:            update_data["tipo_vehiculo"]             = req.tipo_vehiculo
    if req.ventana_inicio:           update_data["ventana_inicio"]            = req.ventana_inicio
    if req.ventana_fin:              update_data["ventana_fin"]               = req.ventana_fin
    if update_data:
        db.table("cotizaciones").update(update_data).eq("cotizacion_id", cotizacion_id).execute()

    total_viverista  = float(cot.get("total_estimado") or 0)
    if total_viverista <= 0:
        raise HTTPException(400, "El total de la cotización es inválido")

    monto_plantas    = round(total_viverista * (1 + MARKUP_PLATAFORMA))
    monto_plataforma = monto_plantas - round(total_viverista)
    flete_cop        = int(req.flete_cop or 0)
    monto_cop        = monto_plantas + flete_cop

    cliente = db.table("clientes").select(
        "nombre_empresa, nombre_representante, whatsapp_numero"
    ).eq("cliente_id", user.cliente_id).limit(1).execute()
    cli   = cliente.data[0] if cliente.data else {}
    nombre = cli.get("nombre_representante") or cli.get("nombre_empresa") or "Cliente"

    items = cot.get("items") or []

    transaccion_id = None
    if cot.get("estado") == "convertida":
        txn_existente = db.table("transacciones_b2b").select(
            "transaccion_id, estado"
        ).eq("cotizacion_id", cotizacion_id).eq("estado", "pendiente").limit(1).execute()
        if txn_existente.data:
            transaccion_id = txn_existente.data[0]["transaccion_id"]

    if not transaccion_id:
        txn_resp = db.table("transacciones_b2b").insert({
            "cliente_id":          user.cliente_id,
            "inventario_id":       items[0].get("inventario_id") if items else None,
            "cantidad":            sum(it.get("cantidad", 0) for it in items),
            "precio_unitario":     float(items[0].get("precio_unitario", 0)) if items else 0,
            "precio_total":        monto_cop,
            "comision_plataforma": monto_plataforma,
            "porcentaje_comision": MARKUP_PLATAFORMA * 100,
            "estado":              "pendiente",
            "cotizacion_id":       cotizacion_id,
        }).execute()

        if not txn_resp.data:
            raise HTTPException(500, "No se pudo crear la transacción")

        transaccion_id = txn_resp.data[0]["transaccion_id"]

        db.table("cotizaciones").update({
            "estado":           "convertida",
            "fecha_conversion": datetime.utcnow().isoformat(),
            "transaccion_id":   transaccion_id,
        }).eq("cotizacion_id", cotizacion_id).execute()

    checkout_req = CheckoutRequest(
        transaccion_id=transaccion_id,
        monto_cop=monto_cop,
        descripcion=f"ViveroOnline · {cot.get('prompt_original') or f'Pedido #{cotizacion_id}'}",
        nombre_cliente=nombre,
        telefono_cliente=cli.get("whatsapp_numero"),
    )
    response_url     = f"{s.app_base_url}/pagos/resultado"
    confirmation_url = f"{s.app_base_url}/api/pagos/confirmacion"
    payload = epayco.build_checkout_payload(checkout_req, response_url, confirmation_url)

    pago_resp = db.table("pagos").insert({
        "transaccion_id":     transaccion_id,
        "monto_total":        monto_cop,
        "moneda":             "COP",
        "estado_pago":        "pendiente",
        "metodo":             "epayco",
        "referencia_externa": payload["invoice"],
        "monto_viverista":    round(total_viverista),
        "monto_plataforma":   monto_plataforma,
    }).execute()

    pago_id = pago_resp.data[0]["pago_id"] if pago_resp.data else None

    return {
        "ok":               True,
        "pago_id":          pago_id,
        "transaccion_id":   transaccion_id,
        "cotizacion_id":    cotizacion_id,
        "referencia":       payload["invoice"],
        "checkout_payload": payload,
        "monto_plantas":    monto_plantas,
        "flete_cop":        flete_cop,
        "monto_cop":        monto_cop,
        "monto_viverista":  round(total_viverista),
        "monto_plataforma": monto_plataforma,
    }
