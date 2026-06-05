"""Gestión del Plan Inteligencia — suscripción mensual $120.000 COP."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from app.auth.deps import UserContext, require_comprador
from app.services.supabase import admin as db_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/suscripciones", tags=["suscripciones"])

MONTO_TOTAL = 120_000
MONTO_BASE  = 100_840
MONTO_IVA   = 19_160
PERIODO_DIAS = 30


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
    activa = s["estado"] == "activa"
    if activa and s.get("fecha_proximo_cobro"):
        try:
            proximo = datetime.fromisoformat(s["fecha_proximo_cobro"].replace("Z", "+00:00"))
            activa = proximo > datetime.now().astimezone()
        except Exception:
            pass
    return {"ok": True, "suscripcion": s, "activa": activa}


@router.post("/contratar")
async def contratar_plan(user: UserContext = Depends(require_comprador)):
    from app.config import get_settings
    db = db_admin()
    s = get_settings()

    sus = db.table("suscripciones").select("suscripcion_id").eq(
        "user_id", user.user_id
    ).eq("plan", "inteligencia").eq("estado", "activa").limit(1).execute()
    if sus.data:
        raise HTTPException(400, "Ya tenés una suscripción activa al Plan Inteligencia")

    cliente = db.table("clientes").select(
        "nombre_representante, nombre_empresa"
    ).eq("cliente_id", user.cliente_id).limit(1).execute()
    cli = cliente.data[0] if cliente.data else {}
    nombre = cli.get("nombre_representante") or cli.get("nombre_empresa") or "Cliente"

    fecha_proximo = (datetime.utcnow() + timedelta(days=PERIODO_DIAS)).isoformat()
    sus_resp = db.table("suscripciones").insert({
        "user_id": user.user_id,
        "plan": "inteligencia",
        "estado": "pendiente",
        "monto_mensual_cop": MONTO_TOTAL,
        "fecha_proximo_cobro": fecha_proximo,
    }).execute()
    suscripcion_id = sus_resp.data[0]["suscripcion_id"] if sus_resp.data else "NEW"

    referencia = f"SUS-{suscripcion_id}-{int(datetime.utcnow().timestamp())}"

    # Obtener public key de ePayco
    epayco_key = ""
    try:
        from app.services.epayco import get_epayco
        ep = get_epayco()
        epayco_key = getattr(ep, "public_key", "") or ""
    except Exception:
        pass

    checkout_payload = {
        "name": "Plan Inteligencia ViveroOnline",
        "description": "Acceso mensual a ubicaciones de viveros en la Sabana de Bogotá",
        "invoice": referencia,
        "currency": "cop",
        "amount": str(MONTO_TOTAL),
        "tax_base": str(MONTO_BASE),
        "tax": str(MONTO_IVA),
        "country": "co",
        "lang": "es",
        "external": "false",
        "extra1": str(suscripcion_id),
        "extra2": str(user.user_id),
        "response": f"{s.app_base_url}/pagos/resultado",
        "confirmation": f"{s.app_base_url}/api/suscripciones/confirmacion",
        "typeSell": "3",
        "periodicityType": "m",
        "frequency": "1",
        "p_cust_id_cliente": epayco_key,
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


@router.post("/confirmacion")
async def confirmacion_pago(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())

    estado = str(data.get("x_response", data.get("x_respuesta", ""))).lower()
    referencia = str(data.get("x_id_factura", data.get("x_ref_payco", "")))
    extra1 = str(data.get("x_extra1", ""))
    extra2 = str(data.get("x_extra2", ""))
    monto = data.get("x_amount", MONTO_TOTAL)

    if estado not in ("aceptada", "aprobada", "accepted", "approved"):
        return {"ok": False, "estado": estado}

    db = db_admin()
    if extra1 and extra1.isdigit():
        fecha_proximo = (datetime.utcnow() + timedelta(days=PERIODO_DIAS)).isoformat()
        db.table("suscripciones").update({
            "estado": "activa",
            "fecha_inicio": datetime.utcnow().isoformat(),
            "fecha_proximo_cobro": fecha_proximo,
            "epayco_subscription_id": referencia,
        }).eq("suscripcion_id", int(extra1)).execute()

    # Notificar por WhatsApp
    try:
        if extra2:
            from app.config import get_settings
            from app.services.whatsapp_meta import send_text_message
            perfil = db.table("perfiles").select(
                "whatsapp_numero"
            ).eq("id", extra2).limit(1).execute()
            if perfil.data and perfil.data[0].get("whatsapp_numero"):
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
