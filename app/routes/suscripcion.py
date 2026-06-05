"""Gestión del Plan Inteligencia — suscripción mensual $120.000 COP.

Flujo:
  POST /api/suscripciones/contratar     → genera checkout ePayco recurrente
  POST /api/suscripciones/confirmacion  → webhook de ePayco confirma cobro
  GET  /api/suscripciones/mi-plan       → estado actual del plan
  POST /api/suscripciones/cancelar      → cancela renovación automática
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from app.auth.deps import UserContext, require_comprador
from app.config import get_settings
from app.services.supabase import admin as db_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/suscripciones", tags=["suscripciones"])

# ── Precio Plan Inteligencia ──────────────────────────────────
PLAN_NOMBRE        = "Plan Inteligencia"
MONTO_TOTAL        = 120_000   # COP con IVA incluido
MONTO_BASE         = 100_840   # sin IVA
MONTO_IVA          = 19_160    # 19% IVA
PERIODO_DIAS       = 30


# ── GET: estado del plan ──────────────────────────────────────

@router.get("/mi-plan")
async def mi_plan(user: UserContext = Depends(require_comprador)):
    db = db_admin()
    sus = db.table("suscripciones").select(
        "suscripcion_id, plan, estado, monto_mensual_cop, "
        "fecha_inicio, fecha_proximo_cobro, fecha_cancelacion"
    ).eq("user_id", user.user_id).eq("plan", "inteligencia").order(
        "fecha_inicio", desc=True
    ).limit(1).execute()

    if not sus.data:
        return {"ok": True, "suscripcion": None, "activa": False}

    s = sus.data[0]
    activa = (
        s["estado"] == "activa"
        and (
            s.get("fecha_proximo_cobro") is None
            or datetime.fromisoformat(
                s["fecha_proximo_cobro"].replace("Z", "+00:00")
            ) > datetime.now().astimezone()
        )
    )
    return {"ok": True, "suscripcion": s, "activa": activa}


# ── POST: generar checkout ePayco ─────────────────────────────

@router.post("/contratar")
async def contratar_plan(user: UserContext = Depends(require_comprador)):
    """Genera el payload ePayco para el checkout del Plan Inteligencia."""
    db = db_admin()
    s = get_settings()
    from app.services.epayco import get_epayco

    epayco = get_epayco()
    if not epayco.is_configured:
        raise HTTPException(503, "Servicio de pagos no configurado")

    # Verificar si ya tiene suscripción activa
    sus = db.table("suscripciones").select("suscripcion_id, estado").eq(
        "user_id", user.user_id
    ).eq("plan", "inteligencia").eq("estado", "activa").limit(1).execute()
    if sus.data:
        raise HTTPException(400, "Ya tenés una suscripción activa al Plan Inteligencia")

    # Datos del cliente
    cliente = db.table("clientes").select(
        "nombre_representante, nombre_empresa, whatsapp_numero"
    ).eq("cliente_id", user.cliente_id).limit(1).execute()
    cli = cliente.data[0] if cliente.data else {}
    nombre = cli.get("nombre_representante") or cli.get("nombre_empresa") or "Cliente"

    # Crear suscripción en estado pendiente
    fecha_proximo = (datetime.utcnow() + timedelta(days=PERIODO_DIAS)).isoformat()
    sus_resp = db.table("suscripciones").insert({
        "user_id": user.user_id,
        "plan": "inteligencia",
        "estado": "pendiente",
        "monto_mensual_cop": MONTO_TOTAL,
        "fecha_proximo_cobro": fecha_proximo,
    }).execute()
    suscripcion_id = sus_resp.data[0]["suscripcion_id"] if sus_resp.data else None

    # Referencia única para el pago
    referencia = f"SUS-{suscripcion_id or 'NEW'}-{int(datetime.utcnow().timestamp())}"

    # Payload ePayco (suscripción recurrente mensual)
    checkout_payload = {
        "name": PLAN_NOMBRE,
        "description": "Acceso mensual a ubicaciones exactas de viveros en la Sabana de Bogotá",
        "invoice": referencia,
        "currency": "cop",
        "amount": str(MONTO_TOTAL),
        "tax_base": str(MONTO_BASE),
        "tax": str(MONTO_IVA),
        "country": "co",
        "lang": "es",
        "external": "false",
        "extra1": str(suscripcion_id or ""),
        "extra2": str(user.user_id),
        "response": f"{s.app_base_url}/pagos/resultado",
        "confirmation": f"{s.app_base_url}/api/suscripciones/confirmacion",
        # Parámetros de suscripción recurrente ePayco
        "typeSell": "3",          # tipo suscripción
        "periodicityType": "m",   # mensual
        "frequency": "1",         # cada 1 mes
        "p_cust_id_cliente": epayco.public_key or "",
    }

    return {
        "ok": True,
        "suscripcion_id": suscripcion_id,
        "referencia": referencia,
        "monto_total": MONTO_TOTAL,
        "monto_base": MONTO_BASE,
        "monto_iva": MONTO_IVA,
        "checkout_payload": checkout_payload,
    }


# ── POST: webhook confirmación ePayco ─────────────────────────

@router.post("/confirmacion")
async def confirmacion_pago(request: Request):
    """ePayco llama a este endpoint cuando procesa un cobro (inicial o renovación)."""
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())

    estado = str(data.get("x_response", data.get("x_respuesta", ""))).lower()
    referencia = str(data.get("x_id_factura", data.get("x_ref_payco", "")))
    extra1 = str(data.get("x_extra1", ""))  # suscripcion_id
    extra2 = str(data.get("x_extra2", ""))  # user_id
    monto = data.get("x_amount", MONTO_TOTAL)

    logger.info(f"Confirmación suscripción: estado={estado} ref={referencia} sus_id={extra1}")

    if estado not in ("aceptada", "aprobada", "accepted", "approved"):
        logger.warning(f"Pago suscripción rechazado: {estado}")
        return {"ok": False, "estado": estado}

    db = db_admin()

    # Activar suscripción
    if extra1 and extra1.isdigit():
        fecha_proximo = (datetime.utcnow() + timedelta(days=PERIODO_DIAS)).isoformat()
        db.table("suscripciones").update({
            "estado": "activa",
            "fecha_inicio": datetime.utcnow().isoformat(),
            "fecha_proximo_cobro": fecha_proximo,
            "epayco_subscription_id": referencia,
            "metadata": {"ultimo_cobro": str(monto), "referencia": referencia},
        }).eq("suscripcion_id", int(extra1)).execute()

    # Registrar pago
    if extra2:
        db.table("pagos").insert({
            "monto_total": int(float(monto or MONTO_TOTAL)),
            "moneda": "COP",
            "estado_pago": "aprobado",
            "metodo": "epayco",
            "referencia_externa": referencia,
            "monto_plataforma": int(float(monto or MONTO_TOTAL)),
            "monto_viverista": 0,
            "metadata": {"tipo": "suscripcion", "user_id": extra2, "sus_id": extra1},
        }).execute()

    # Notificar por WhatsApp
    try:
        if extra2:
            perfil = db.table("perfiles").select(
                "whatsapp_numero, nombre_display"
            ).eq("id", extra2).limit(1).execute()
            if perfil.data and perfil.data[0].get("whatsapp_numero"):
                from app.services.whatsapp_meta import send_text_message
                from app.config import get_settings
                base = get_settings().app_base_url
                await send_text_message(
                    perfil.data[0]["whatsapp_numero"],
                    f"✅ *Plan Inteligencia activado — ViveroOnline*\n\n"
                    f"Ya podés ver la ubicación exacta de todos los viveros.\n"
                    f"Tu próximo cobro es en 30 días: $120.000 COP\n\n"
                    f"👉 {base}/marketplace"
                )
    except Exception:
        pass

    return {"ok": True, "estado": "activa"}


# ── POST: cancelar suscripción ────────────────────────────────

@router.post("/cancelar")
async def cancelar_plan(user: UserContext = Depends(require_comprador)):
    db = db_admin()
    sus = db.table("suscripciones").select("suscripcion_id").eq(
        "user_id", user.user_id
    ).eq("plan", "inteligencia").eq("estado", "activa").limit(1).execute()

    if not sus.data:
        raise HTTPException(404, "No tenés una suscripción activa")

    db.table("suscripciones").update({
        "estado": "cancelada",
        "fecha_cancelacion": datetime.utcnow().isoformat(),
    }).eq("suscripcion_id", sus.data[0]["suscripcion_id"]).execute()

    return {"ok": True, "mensaje": "Suscripción cancelada. Mantenés acceso hasta el próximo vencimiento."}
