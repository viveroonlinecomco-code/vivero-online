"""Plan Inteligencia — $120.000 COP/mes con IVA."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from app.auth.deps import UserContext, require_comprador
from app.services.supabase import admin as db_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/suscripciones", tags=["suscripciones"])

MONTO_TOTAL  = 120_000
MONTO_BASE   = 100_840
MONTO_IVA    = 19_160
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
    return {"ok": True, "suscripcion": s, "activa": activa}


@router.post("/contratar")
async def contratar_plan(user: UserContext = Depends(require_comprador)):
    import os
    db = db_admin()
    base = os.environ.get("APP_BASE_URL", "https://app.viveroonline.com.co")
    epayco_key = os.environ.get("EPAYCO_PUBLIC_KEY", "")

    sus = db.table("suscripciones").select("suscripcion_id").eq(
        "user_id", user.user_id
    ).eq("plan", "inteligencia").eq("estado", "activa").limit(1).execute()
    if sus.data:
        raise HTTPException(400, "Ya tenés una suscripción activa")

    fecha_proximo = (datetime.utcnow() + timedelta(days=PERIODO_DIAS)).isoformat()
    sus_resp = db.table("suscripciones").insert({
        "user_id": user.user_id,
        "plan": "inteligencia",
        "estado": "pendiente",
        "monto_mensual_cop": MONTO_TOTAL,
        "fecha_proximo_cobro": fecha_proximo,
    }).execute()
    sus_id = sus_resp.data[0]["suscripcion_id"] if sus_resp.data else "NEW"
    referencia = f"SUS-{sus_id}-{int(datetime.utcnow().timestamp())}"

    return {
        "ok": True,
        "suscripcion_id": sus_id,
        "referencia": referencia,
        "monto_total": MONTO_TOTAL,
        "monto_base": MONTO_BASE,
        "monto_iva": MONTO_IVA,
        "checkout_payload": {
            "name": "Plan Inteligencia ViveroOnline",
            "description": "Acceso mensual a ubicaciones de viveros — Sabana de Bogotá",
            "invoice": referencia,
            "currency": "cop",
            "amount": str(MONTO_TOTAL),
            "tax_base": str(MONTO_BASE),
            "tax": str(MONTO_IVA),
            "country": "co",
            "lang": "es",
            "external": "false",
            "extra1": str(sus_id),
            "extra2": str(user.user_id),
            "response": f"{base}/pagos/resultado",
            "confirmation": f"{base}/api/suscripciones/confirmacion",
            "typeSell": "3",
            "periodicityType": "m",
            "frequency": "1",
            "p_cust_id_cliente": epayco_key,
        },
    }


@router.post("/confirmacion")
async def confirmacion_pago(request: Request):
    import os
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())

    estado = str(data.get("x_response", "")).lower()
    extra1 = str(data.get("x_extra1", ""))

    if estado not in ("aceptada", "aprobada", "accepted", "approved"):
        return {"ok": False, "estado": estado}

    db = db_admin()
    if extra1 and extra1.isdigit():
        fecha_proximo = (datetime.utcnow() + timedelta(days=PERIODO_DIAS)).isoformat()
        db.table("suscripciones").update({
            "estado": "activa",
            "fecha_inicio": datetime.utcnow().isoformat(),
            "fecha_proximo_cobro": fecha_proximo,
        }).eq("suscripcion_id", int(extra1)).execute()

    return {"ok": True, "estado": "activa"}


@router.post("/cancelar")
async def cancelar_plan(user: UserContext = Depends(require_comprador)):
    db = db_admin()
    sus = db.table("suscripciones").select("suscripcion_id").eq(
        "user_id", user.user_id
    ).eq("plan", "inteligencia").eq("estado", "activa").limit(1).execute()

    if not sus.data:
        raise HTTPException(404, "No tenés suscripción activa")

    db.table("suscripciones").update({
        "estado": "cancelada",
        "fecha_cancelacion": datetime.utcnow().isoformat(),
    }).eq("suscripcion_id", sus.data[0]["suscripcion_id"]).execute()
    return {"ok": True}
