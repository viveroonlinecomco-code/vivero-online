"""Flujo de aprobación y pago de cotizaciones.

Flujo de estados:
  borrador → enviada (comprador solicita)
           → aceptada (viverista aprueba)  → checkout → pagada
           → rechazada (viverista rechaza)

Modelo de precios:
  - precio_mayorista en BD = precio BASE del viverista (lo que él recibe)
  - El comprador paga: total_cotizacion × 1.18 (18% de markup de plataforma)
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

MARKUP_PLATAFORMA = 0.18  # 18% que se suma al precio base del viverista


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

    # Notificar al viverista
    try:
        base = get_settings().app_base_url
        items = cot.get("items") or []
        inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
        if inv_ids:
            inv_resp = db.table("inventario").select("vivero_id").in_("inventario_id", inv_ids).execute()
            vivero_ids = list({r["vivero_id"] for r in (inv_resp.data or []) if r.get("vivero_id")})
            if len(vivero_ids) == 1:
                v = db.table("viveros").select("whatsapp_numero, nombre_vivero").eq(
                    "vivero_id", vivero_ids[0]
                ).limit(1).execute()
                if v.data and v.data[0].get("whatsapp_numero"):
                    total_base = int(float(cot.get("total_estimado") or 0))
                    total_comprador = round(total_base * (1 + MARKUP_PLATAFORMA))
                    nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"
                    msg = (
                        f"🌿 *Nueva solicitud de cotización — ViveroOnline*\n\n"
                        f"Proyecto: {nombre_proyecto}\n"
                        f"Items: {len(items)} productos\n"
                        f"Tu precio base: ${total_base:,} COP\n"
                        f"Precio al comprador: ${total_comprador:,} COP\n\n"
                        f"Ingresá a tu panel para aprobar o rechazar:\n"
                        f"{base}/viverista"
                    )
                    send_text_message(v.data[0]["whatsapp_numero"], msg)
    except Exception:
        pass

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
            "total_estimado": total_base,  # precio base del viverista
            "total_comprador": round(total_base * (1 + MARKUP_PLATAFORMA)),  # lo que paga el comprador
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
        inv_resp = db.table("inventario").select("inventario_id, vivero_id").in_("inventario_id", inv_ids).execute()
        if not any(r.get("vivero_id") == user.vivero_id for r in (inv_resp.data or [])):
            raise HTTPException(403, "Esta cotización no contiene items de tu vivero")

    db.table("cotizaciones").update({"estado": "aceptada"}).eq("cotizacion_id", cotizacion_id).execute()

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
                f"✅ *¡Tu cotización fue aprobada! — ViveroOnline*\n\n"
                f"Proyecto: {nombre_proyecto}\n"
                f"Total a pagar: ${total_comprador:,} COP\n\n"
                f"Ya podés proceder con el pago:\n"
                f"{base}/comprador"
            )
            send_text_message(cliente.data[0]["whatsapp_numero"], msg)
    except Exception:
        pass

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
        inv_resp = db.table("inventario").select("inventario_id, vivero_id").in_("inventario_id", inv_ids).execute()
        if not any(r.get("vivero_id") == user.vivero_id for r in (inv_resp.data or [])):
            raise HTTPException(403, "Esta cotización no contiene items de tu vivero")

    db.table("cotizaciones").update({
        "estado": "rechazada",
        "notas_agente": req.motivo or "Rechazada por el viverista",
    }).eq("cotizacion_id", cotizacion_id).execute()

    try:
        base = get_settings().app_base_url
        cliente = db.table("clientes").select("whatsapp_numero").eq(
            "cliente_id", cot["cliente_id"]
        ).limit(1).execute()
        if cliente.data and cliente.data[0].get("whatsapp_numero"):
            nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"
            motivo_txt = f"\nMotivo: {req.motivo}" if req.motivo else ""
            msg = (
                f"❌ *Cotización no disponible — ViveroOnline*\n\n"
                f"Proyecto: {nombre_proyecto}{motivo_txt}\n\n"
                f"Podés buscar alternativas en el marketplace:\n"
                f"{base}/marketplace"
            )
            send_text_message(cliente.data[0]["whatsapp_numero"], msg)
    except Exception:
        pass

    return {"ok": True, "estado": "rechazada", "cotizacion_id": cotizacion_id}


# ═══════════ 5. COMPRADOR: Iniciar pago (con markup 18%) ═══════════

class CheckoutReq(BaseModel):
    ciudad_entrega: Optional[str] = None
    fecha_entrega_deseada: Optional[str] = None
    notas: Optional[str] = None


@router.post("/{cotizacion_id}/checkout")
async def iniciar_checkout(
    cotizacion_id: int, req: CheckoutReq, user: UserContext = Depends(require_comprador)
):
    """Convierte cotización aprobada en transacción con markup del 18%."""
    from app.services.epayco import get_epayco, CheckoutRequest

    db = db_admin()
    cot = _get_cotizacion(db, cotizacion_id)

    if cot["cliente_id"] != user.cliente_id:
        raise HTTPException(403, "No podés pagar esta cotización")
    if cot["estado"] != "aceptada":
        raise HTTPException(400, f"Solo se pueden pagar cotizaciones aceptadas. Estado: {cot['estado']}")

    s = get_settings()
    epayco = get_epayco()
    if not epayco.is_configured:
        raise HTTPException(503, "Servicio de pagos no configurado")

    update_data = {}
    if req.ciudad_entrega:
        update_data["ciudad_entrega"] = req.ciudad_entrega
    if req.fecha_entrega_deseada:
        update_data["fecha_entrega_deseada"] = req.fecha_entrega_deseada
    if req.notas:
        update_data["notas_cliente"] = req.notas
    if update_data:
        db.table("cotizaciones").update(update_data).eq("cotizacion_id", cotizacion_id).execute()

    # Precio base del viverista (lo que está guardado en BD)
    total_viverista = float(cot.get("total_estimado") or 0)
    if total_viverista <= 0:
        raise HTTPException(400, "El total de la cotización es inválido")

    # El comprador paga: precio_base × 1.18
    monto_cop = round(total_viverista * (1 + MARKUP_PLATAFORMA))
    monto_plataforma = monto_cop - round(total_viverista)
    monto_viverista_final = round(total_viverista)

    cliente = db.table("clientes").select(
        "nombre_empresa, nombre_representante, whatsapp_numero"
    ).eq("cliente_id", user.cliente_id).limit(1).execute()
    cli = cliente.data[0] if cliente.data else {}
    nombre = cli.get("nombre_representante") or cli.get("nombre_empresa") or "Cliente"

    items = cot.get("items") or []
    txn_resp = db.table("transacciones_b2b").insert({
        "cliente_id": user.cliente_id,
        "inventario_id": items[0].get("inventario_id") if items else None,
        "cantidad": sum(it.get("cantidad", 0) for it in items),
        "precio_unitario": float(items[0].get("precio_unitario", 0)) if items else 0,
        "precio_total": monto_cop,              # lo que paga el comprador (con markup)
        "comision_plataforma": monto_plataforma, # el 18%
        "porcentaje_comision": MARKUP_PLATAFORMA * 100,  # 18.0
        "estado": "pendiente",
        "cotizacion_id": cotizacion_id,
    }).execute()

    if not txn_resp.data:
        raise HTTPException(500, "No se pudo crear la transacción")

    transaccion_id = txn_resp.data[0]["transaccion_id"]

    db.table("cotizaciones").update({
        "estado": "convertida",
        "fecha_conversion": datetime.utcnow().isoformat(),
        "transaccion_id": transaccion_id,
    }).eq("cotizacion_id", cotizacion_id).execute()

    checkout_req = CheckoutRequest(
        transaccion_id=transaccion_id,
        monto_cop=monto_cop,
        descripcion=f"ViveroOnline · {cot.get('prompt_original') or f'Pedido #{cotizacion_id}'}",
        nombre_cliente=nombre,
        telefono_cliente=cli.get("whatsapp_numero"),
    )
    response_url = f"{s.app_base_url}/pagos/resultado"
    confirmation_url = f"{s.app_base_url}/api/pagos/confirmacion"
    payload = epayco.build_checkout_payload(checkout_req, response_url, confirmation_url)

    pago_resp = db.table("pagos").insert({
        "transaccion_id": transaccion_id,
        "monto_total": monto_cop,
        "moneda": "COP",
        "estado_pago": "pendiente",
        "metodo": "epayco",
        "referencia_externa": payload["invoice"],
        "monto_viverista": monto_viverista_final,  # precio base (lo que recibe el vivero)
        "monto_plataforma": monto_plataforma,       # el 18%
    }).execute()

    pago_id = pago_resp.data[0]["pago_id"] if pago_resp.data else None

    return {
        "ok": True,
        "pago_id": pago_id,
        "transaccion_id": transaccion_id,
        "cotizacion_id": cotizacion_id,
        "referencia": payload["invoice"],
        "checkout_payload": payload,
        "monto_cop": monto_cop,                  # total que cobra ePayco
        "monto_viverista": monto_viverista_final, # lo que recibe el vivero
        "monto_plataforma": monto_plataforma,     # el 18% de ViveroOnline
    }
