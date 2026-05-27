"""Endpoints para gestión de suscripciones al plan Inteligencia.

Flujo de pago:
1. Comprador hace POST /api/suscripcion/iniciar-pago
2. Si ya tiene suscripción activa → 400
3. Si ePayco no configurado → 503 con info para activación manual via WhatsApp
4. Si ePayco configurado → crea/reusa suscripción pendiente_pago + pago + payload checkout
5. Webhook de ePayco activa la suscripción por 30 días cuando confirma el pago
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import UserContext, require_user
from app.config import get_settings
from app.services.epayco import CheckoutRequest, get_epayco
from app.services.supabase import admin


router = APIRouter(prefix="/api/suscripcion", tags=["suscripcion"])

logger = logging.getLogger(__name__)

MONTO_PLAN_INTELIGENCIA_COP = 79900
WHATSAPP_ACTIVACION = "+57 310 224 6099"
WHATSAPP_URL = (
    "https://wa.me/573102246099?text=Hola%2C+quiero+suscribirme+al+plan+Inteligencia"
)


def _extraer_user_id(user) -> Optional[str]:
    """Extrae user_id del UserContext (defensivo: id, user_id, sub, uid)."""
    for attr in ("id", "user_id", "sub", "uid"):
        val = getattr(user, attr, None)
        if val:
            return str(val)
    return None


@router.get("/estado")
async def estado_suscripcion(user: UserContext = Depends(require_user)):
    """Estado de suscripción del usuario actual.

    Siempre responde algo válido (nunca falla), aunque no haya suscripción.
    """
    user_id = _extraer_user_id(user)
    if not user_id:
        return {"tiene_suscripcion": False, "plan": "free"}

    try:
        db = admin()
        resp = (
            db.table("suscripciones")
            .select("suscripcion_id, plan, estado, fecha_inicio, fecha_proximo_cobro, fecha_cancelacion")
            .eq("user_id", user_id)
            .in_("estado", ["activa", "pendiente_pago"])
            .order("fecha_inicio", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return {"tiene_suscripcion": False, "plan": "free"}

        s = resp.data[0]
        activa = s["estado"] == "activa"
        return {
            "tiene_suscripcion": activa,
            "plan": s["plan"] if activa else "free",
            "suscripcion_id": s["suscripcion_id"],
            "estado": s["estado"],
            "fecha_inicio": s.get("fecha_inicio"),
            "fecha_proximo_cobro": s.get("fecha_proximo_cobro"),
            "fecha_cancelacion": s.get("fecha_cancelacion"),
        }
    except Exception as e:
        logger.error("Error en estado_suscripcion: %r", e)
        return {"tiene_suscripcion": False, "plan": "free"}


@router.post("/iniciar-pago")
async def iniciar_pago_suscripcion(user: UserContext = Depends(require_user)):
    """Inicia el flujo de pago para el plan Inteligencia.

    Devuelve el payload del checkout ePayco que el frontend abre en modal.
    Si ePayco no esta configurado, devuelve 503 con info para activacion manual.
    """
    if user.rol not in ("comprador", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Solo los compradores pueden suscribirse al plan Inteligencia",
        )

    user_id = _extraer_user_id(user)
    if not user_id:
        raise HTTPException(500, detail="No se pudo determinar tu identidad")

    db = admin()

    # 1. Validar que no tenga ya suscripcion activa
    existing = (
        db.table("suscripciones")
        .select("suscripcion_id")
        .eq("user_id", user_id)
        .eq("estado", "activa")
        .limit(1)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "already_subscribed",
                "mensaje": "Ya tenes una suscripcion activa al plan Inteligencia.",
            },
        )

    # 2. ePayco configurado?
    s = get_settings()
    epayco = get_epayco()
    if not epayco.is_configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "epayco_not_configured",
                "mensaje": (
                    "El sistema de pagos esta en activacion. "
                    "Contactanos por WhatsApp para activar tu plan manualmente."
                ),
                "whatsapp_url": WHATSAPP_URL,
                "whatsapp_numero": WHATSAPP_ACTIVACION,
            },
        )

    # 3. Crear o reusar suscripcion pendiente_pago
    pendiente = (
        db.table("suscripciones")
        .select("suscripcion_id")
        .eq("user_id", user_id)
        .eq("estado", "pendiente_pago")
        .limit(1)
        .execute()
    )
    if pendiente.data:
        suscripcion_id = pendiente.data[0]["suscripcion_id"]
    else:
        new_sus = (
            db.table("suscripciones")
            .insert({
                "user_id": user_id,
                "plan": "inteligencia",
                "estado": "pendiente_pago",
                "monto_mensual_cop": MONTO_PLAN_INTELIGENCIA_COP,
                "metadata": {"source": "self_checkout"},
            })
            .execute()
        )
        if not new_sus.data:
            raise HTTPException(500, detail="No se pudo crear la suscripcion")
        suscripcion_id = new_sus.data[0]["suscripcion_id"]

    # 4. Datos del comprador para precompletar checkout
    nombre = "Comprador"
    telefono = None
    cliente_id = getattr(user, "cliente_id", None)
    if cliente_id:
        cliente_resp = (
            db.table("clientes")
            .select("nombre_empresa, nombre_representante, whatsapp_numero")
            .eq("cliente_id", cliente_id)
            .limit(1)
            .execute()
        )
        if cliente_resp.data:
            c = cliente_resp.data[0]
            nombre = c.get("nombre_representante") or c.get("nombre_empresa") or "Comprador"
            telefono = c.get("whatsapp_numero")

    # 5. Construir payload ePayco
    # IMPORTANTE: pasamos suscripcion_id como transaccion_id "virtual". La tabla pagos.tipo
    # distingue despues si fue suscripcion o transaccion B2B real.
    response_url = f"{s.app_base_url}/pagos/resultado"
    confirmation_url = f"{s.app_base_url}/api/pagos/confirmacion"

    checkout_req = CheckoutRequest(
        transaccion_id=suscripcion_id,
        monto_cop=MONTO_PLAN_INTELIGENCIA_COP,
        descripcion="ViveroOnline · Plan Inteligencia (30 dias)",
        nombre_cliente=nombre,
        telefono_cliente=telefono,
    )
    payload = epayco.build_checkout_payload(checkout_req, response_url, confirmation_url)
    referencia = payload["invoice"]

    # 6. Registrar pago en BD con tipo='suscripcion'
    pago_resp = (
        db.table("pagos")
        .insert({
            "tipo": "suscripcion",
            "suscripcion_id": suscripcion_id,
            "transaccion_id": None,
            "monto_total": MONTO_PLAN_INTELIGENCIA_COP,
            "moneda": "COP",
            "estado_pago": "pendiente",
            "metodo": "epayco",
            "referencia_externa": referencia,
            "monto_viverista": 0,
            "monto_plataforma": MONTO_PLAN_INTELIGENCIA_COP,
        })
        .execute()
    )
    if not pago_resp.data:
        raise HTTPException(500, detail="No se pudo registrar el pago")

    return {
        "ok": True,
        "pago_id": pago_resp.data[0]["pago_id"],
        "suscripcion_id": suscripcion_id,
        "referencia": referencia,
        "checkout_payload": payload,
        "monto_cop": MONTO_PLAN_INTELIGENCIA_COP,
    }


@router.post("/cancelar")
async def cancelar_suscripcion(user: UserContext = Depends(require_user)):
    """Cancela la suscripcion activa del usuario.

    Marca como 'cancelada' pero mantiene acceso hasta fecha_proximo_cobro.
    """
    user_id = _extraer_user_id(user)
    if not user_id:
        raise HTTPException(500, detail="No se pudo determinar tu identidad")

    db = admin()
    resp = (
        db.table("suscripciones")
        .select("suscripcion_id, fecha_proximo_cobro")
        .eq("user_id", user_id)
        .eq("estado", "activa")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise HTTPException(404, detail="No tenes suscripcion activa para cancelar")

    sus = resp.data[0]
    db.table("suscripciones").update({
        "estado": "cancelada",
        "fecha_cancelacion": "now()",
    }).eq("suscripcion_id", sus["suscripcion_id"]).execute()

    return {
        "ok": True,
        "mensaje": "Tu suscripcion fue cancelada. Mantenes acceso hasta tu proxima fecha de cobro.",
        "acceso_hasta": sus.get("fecha_proximo_cobro"),
    }
