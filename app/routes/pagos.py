"""Rutas de pagos vía ePayco.

Flujo:
1. Comprador con cotización aprobada → POST /api/pagos/iniciar
2. Backend crea registro en `pagos` (estado=pendiente) + payload ePayco firmado
3. Frontend abre el checkout de ePayco con ese payload
4. Usuario completa el pago → ePayco redirige a /pagos/resultado (response_url)
5. ePayco notifica POST /api/pagos/confirmacion (confirmation_url)
6. Backend valida firma SHA256 → actualiza pago + transacción
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.auth.deps import UserContext, require_user
from app.config import get_settings
from app.services.epayco import (
    CheckoutRequest, EpaycoConfirmation,
    get_epayco, map_epayco_state_to_db,
)
from app.services.supabase import admin


router = APIRouter(prefix="/api/pagos", tags=["pagos"])


# ─────────────────── INICIAR PAGO ───────────────────

class IniciarPagoRequest(BaseModel):
    transaccion_id: int = Field(..., description="ID de transaccion_b2b a pagar")


class IniciarPagoResponse(BaseModel):
    ok: bool
    pago_id: int
    referencia: str
    checkout_payload: dict      # Lo que el frontend pasa al SDK ePayco
    monto_cop: int


@router.post("/iniciar", response_model=IniciarPagoResponse)
async def iniciar_pago(req: IniciarPagoRequest, user: UserContext = Depends(require_user)):
    """Crea registro de pago + payload firmado para abrir ePayco Checkout."""
    s = get_settings()
    epayco = get_epayco()
    if not epayco.is_configured:
        raise HTTPException(503, detail="Servicio de pagos no configurado")

    db = admin()

    # 1. Validar transacción y dueño
    txn_resp = db.table("transacciones_b2b").select(
        "transaccion_id, cliente_id, precio_total, estado, "
        "clientes(nombre_empresa, nombre_representante, whatsapp_numero)"
    ).eq("transaccion_id", req.transaccion_id).limit(1).execute()
    if not txn_resp.data:
        raise HTTPException(404, detail="Transacción no encontrada")
    txn = txn_resp.data[0]

    # Solo el comprador o admin pueden iniciar pago
    if user.rol == "comprador" and txn["cliente_id"] != user.cliente_id:
        raise HTTPException(403, detail="No puedes pagar esta transacción")
    if user.rol not in ("comprador", "admin"):
        raise HTTPException(403, detail="Solo compradores pueden iniciar pagos")

    if txn["estado"] in ("pagada", "completada"):
        raise HTTPException(400, detail="Esta transacción ya fue pagada")

    monto_cop = int(float(txn["precio_total"]))
    cliente = txn.get("clientes") or {}

    # 2. Construir payload ePayco
    response_url = f"{s.app_base_url}/pagos/resultado"
    confirmation_url = f"{s.app_base_url}/api/pagos/confirmacion"
    checkout_req = CheckoutRequest(
        transaccion_id=req.transaccion_id,
        monto_cop=monto_cop,
        descripcion=f"ViveroOnline · Transacción #{req.transaccion_id}",
        nombre_cliente=cliente.get("nombre_representante") or cliente.get("nombre_empresa") or "Cliente",
        telefono_cliente=cliente.get("whatsapp_numero"),
    )
    payload = epayco.build_checkout_payload(checkout_req, response_url, confirmation_url)
    referencia = payload["invoice"]

    # 3. Crear registro en pagos (estado pendiente)
    # Comisión plataforma: 5% (configurable después)
    monto_plataforma = round(monto_cop * 0.05, 2)
    monto_viverista = monto_cop - monto_plataforma

    pago_resp = db.table("pagos").insert({
        "transaccion_id": req.transaccion_id,
        "monto_total": monto_cop,
        "moneda": "COP",
        "estado_pago": "pendiente",
        "metodo": "epayco",
        "referencia_externa": referencia,
        "monto_viverista": monto_viverista,
        "monto_plataforma": monto_plataforma,
    }).execute()
    if not pago_resp.data:
        raise HTTPException(500, detail="No se pudo registrar el pago")

    return IniciarPagoResponse(
        ok=True,
        pago_id=pago_resp.data[0]["pago_id"],
        referencia=referencia,
        checkout_payload=payload,
        monto_cop=monto_cop,
    )


# ─────────────────── WEBHOOK CONFIRMACIÓN ───────────────────

@router.post("/confirmacion")
async def confirmar_pago(
    request: Request,
    x_id_factura: str = Form(""),
    x_ref_payco: str = Form(""),
    x_amount: str = Form("0"),
    x_currency_code: str = Form("COP"),
    x_response: str = Form(""),
    x_response_reason_text: str = Form(""),
    x_transaction_id: str = Form(""),
    x_signature: str = Form(""),
    x_id_invoice: Optional[str] = Form(None),
):
    """Webhook que ePayco llama cuando se confirma/rechaza un pago.

    Es la fuente de verdad: aunque el response_url falle, este webhook
    asegura que sepamos el estado real del pago.
    """
    epayco = get_epayco()
    db = admin()

    conf = EpaycoConfirmation(
        x_id_factura=x_id_factura,
        x_id_invoice=x_id_invoice,
        x_ref_payco=x_ref_payco,
        x_amount=x_amount,
        x_currency_code=x_currency_code,
        x_response=x_response,
        x_response_reason_text=x_response_reason_text,
        x_transaction_id=x_transaction_id,
        x_signature=x_signature,
    )

    # 1. Validar firma
    if not epayco.validate_signature(conf):
        # Logueamos pero respondemos 200 igual (ePayco reintenta si no responde 2xx)
        try:
            db.table("log_ia").insert({
                "tipo_operacion": "epayco_invalid_signature",
                "input_data": conf.model_dump(),
            }).execute()
        except Exception:
            pass
        return {"ok": False, "reason": "invalid_signature"}

    # 2. Buscar pago por referencia
    pago_resp = db.table("pagos").select("pago_id, transaccion_id, estado_pago").eq(
        "referencia_externa", x_id_factura
    ).limit(1).execute()
    if not pago_resp.data:
        return {"ok": False, "reason": "pago_no_encontrado"}
    pago = pago_resp.data[0]

    # 3. Actualizar pago
    new_state = map_epayco_state_to_db(x_response)
    db.table("pagos").update({
        "estado_pago": new_state,
        "epayco_id": x_ref_payco,
        "fecha_confirmacion": "now()",
        "webhook_payload": conf.model_dump(),
    }).eq("pago_id", pago["pago_id"]).execute()

    # 4. Si fue aprobado, marcar transacción como pagada
    if new_state == "aprobado":
        db.table("transacciones_b2b").update({
            "estado": "pagada",
        }).eq("transaccion_id", pago["transaccion_id"]).execute()

    return {"ok": True, "estado": new_state}


# ─────────────────── PÁGINA RESULTADO ───────────────────

@router.get("/estado/{pago_id}")
async def estado_pago(pago_id: int, user: UserContext = Depends(require_user)):
    """Consulta el estado actual del pago. Útil para polling desde la página de resultado."""
    db = admin()
    resp = db.table("pagos").select(
        "pago_id, transaccion_id, monto_total, estado_pago, "
        "metodo, referencia_externa, fecha_creacion, fecha_confirmacion"
    ).eq("pago_id", pago_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(404, detail="Pago no encontrado")
    return {"ok": True, "pago": resp.data[0]}
