"""Endpoints del plan Inteligencia (suscripción paga del comprador).

Gateado por la función SQL `tiene_suscripcion_activa('inteligencia')`.
Si el usuario NO tiene suscripción activa, devuelve 403 con un código
machine-readable que el frontend usa para mostrar el paywall.

Las 4 vistas v_public_* fueron revocadas a anon/authenticated tras
eliminar /inversores. Solo service_role puede leerlas — por eso acá
usamos `admin()` (cliente con service_role).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import UserContext, require_user
from app.services.supabase import admin


router = APIRouter(prefix="/api/mi-cuenta/inteligencia", tags=["inteligencia"])


# ─────────────────── HELPERS ───────────────────

def _verificar_suscripcion(user_id: str, plan: str = "inteligencia") -> bool:
    """Llama a la función SQL tiene_suscripcion_activa() vía RPC.

    Importante: la función usa auth.uid() internamente, pero como acá
    invocamos con service_role la sesión Postgres no tiene jwt. Por eso
    hacemos la query directa contra la tabla con filtro explícito por user_id.
    """
    db = admin()
    resp = (
        db.table("suscripciones")
        .select("suscripcion_id")
        .eq("user_id", user_id)
        .in_("plan", [plan, "pro"])
        .eq("estado", "activa")
        .limit(1)
        .execute()
    )
    return bool(resp.data)


# ─────────────────── ENDPOINT: MERCADO ───────────────────

@router.get("/mercado")
async def get_mercado(user: UserContext = Depends(require_user)):
    """Dashboard de mercado: flywheel, crecimiento, demanda municipio, top especies.

    Gateado por suscripción Inteligencia activa (admin bypassa).
    """
    # Admin puede ver siempre (para auditoría / testing)
    if user.rol != "admin":
        if not _verificar_suscripcion(user.id, plan="inteligencia"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "subscription_required",
                    "plan_requerido": "inteligencia",
                    "mensaje": (
                        "Necesitás el plan Inteligencia para acceder al "
                        "dashboard de mercado. Suscribite desde /mi-cuenta/suscripcion."
                    ),
                },
            )

    db = admin()

    flywheel_resp = db.table("v_public_flywheel").select("*").execute()
    crecimiento_resp = db.table("v_public_crecimiento").select("*").execute()
    demanda_resp = db.table("v_public_demanda_municipio").select("*").execute()
    especies_resp = db.table("v_public_top_especies").select("*").limit(20).execute()

    k = (flywheel_resp.data or [{}])[0]

    return {
        "ok": True,
        "version": "1.0",
        "kpis": {
            "gmv_aprox_cop": int(k.get("gmv_aprox_cop", 0) or 0),
            "transacciones_validas": k.get("transacciones_validas", 0),
            "compradores_activos": k.get("compradores_activos", 0),
            "viveristas_activos": k.get("viveristas_activos", 0),
            "viveros_registrados": k.get("viveros_registrados", 0),
            "items_disponibles": k.get("items_disponibles", 0),
            "txn_ultimas_24h": k.get("txn_ultimas_24h", 0),
        },
        "crecimiento_semanal": crecimiento_resp.data or [],
        "demanda_municipio": demanda_resp.data or [],
        "top_especies": especies_resp.data or [],
    }
