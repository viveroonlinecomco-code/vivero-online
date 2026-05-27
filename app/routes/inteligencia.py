"""Endpoints del plan Inteligencia (suscripcion paga del comprador)."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import UserContext, require_user
from app.services.supabase import admin


router = APIRouter(prefix="/api/mi-cuenta/inteligencia", tags=["inteligencia"])

logger = logging.getLogger(__name__)


def _extraer_user_id(user) -> Optional[str]:
    """Extrae el user_id del UserContext sea cual sea el nombre del campo."""
    for attr in ("id", "user_id", "sub", "uid"):
        val = getattr(user, attr, None)
        if val:
            return str(val)
    return None


def _verificar_suscripcion(user_id, plan: str = "inteligencia") -> bool:
    """Verifica si el user tiene suscripcion activa del plan dado (o superior)."""
    if not user_id:
        return False

    try:
        db = admin()
        resp = (
            db.table("suscripciones")
            .select("suscripcion_id, plan, estado, fecha_proximo_cobro")
            .eq("user_id", str(user_id))
            .eq("estado", "activa")
            .execute()
        )
        rows = resp.data or []
        planes_validos = {plan, "pro"}
        for r in rows:
            if r.get("plan") in planes_validos:
                return True
        return False
    except Exception as e:
        logger.error("Error en _verificar_suscripcion: %r", e)
        return False


@router.get("/mercado")
async def get_mercado(user: UserContext = Depends(require_user)):
    """Dashboard de mercado: flywheel, crecimiento, demanda municipio, top especies."""
    user_id = _extraer_user_id(user)

    if user.rol != "admin":
        if not _verificar_suscripcion(user_id, plan="inteligencia"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "subscription_required",
                    "plan_requerido": "inteligencia",
                    "mensaje": "Necesitas el plan Inteligencia para acceder al dashboard de mercado.",
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


@router.get("/secop")
async def get_secop(
    especie: Optional[str] = None,
    ciudad: Optional[str] = None,
    limite: int = 50,
    solo_relevantes: bool = False,
    user: UserContext = Depends(require_user),
):
    """Feed de procesos SECOP clasificados.

    Por default devuelve TODOS los procesos recientes (con clasificacion IA si existe).
    Usar solo_relevantes=true para filtrar solo los marcados como relevantes.
    """
    user_id = _extraer_user_id(user)

    if user.rol != "admin":
        if not _verificar_suscripcion(user_id, plan="inteligencia"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "subscription_required",
                    "plan_requerido": "inteligencia",
                    "mensaje": "Necesitas el plan Inteligencia para acceder al feed SECOP.",
                },
            )

    limite = max(1, min(limite, 200))

    db = admin()

    query = (
        db.table("secop_procesos")
        .select(
            "proceso_id, entidad, objeto, cuantia_proceso, departamento, ciudad, "
            "fecha_publicacion, url_proceso, estado_proceso, modalidad_contratacion, "
            "secop_clasificacion!left(especies_mencionadas, cantidad_estimada, "
            "altura_estimada_cm, tipo_proyecto, es_relevante, confianza, modelo_usado)"
        )
        .order("fecha_publicacion", desc=True)
        .limit(limite)
    )

    if ciudad:
        query = query.ilike("ciudad", "%" + ciudad + "%")

    resp = query.execute()
    procesos = resp.data or []

    resultado = []
    for p in procesos:
        clasif_raw = p.get("secop_clasificacion") or []
        if isinstance(clasif_raw, list):
            clasif = clasif_raw[0] if clasif_raw else None
        elif isinstance(clasif_raw, dict):
            clasif = clasif_raw
        else:
            clasif = None

        es_relevante = clasif.get("es_relevante") if clasif else None

        if solo_relevantes and es_relevante is not True:
            continue

        if especie:
            especies = (clasif or {}).get("especies_mencionadas") or []
            especies_lower = [str(e).lower() for e in especies]
            if not any(especie.lower() in e for e in especies_lower):
                continue

        resultado.append({
            "proceso_id": p.get("proceso_id"),
            "entidad": p.get("entidad"),
            "objeto": p.get("objeto"),
            "cuantia_cop": p.get("cuantia_proceso"),
            "departamento": p.get("departamento"),
            "ciudad": p.get("ciudad"),
            "fecha_publicacion": p.get("fecha_publicacion"),
            "url_proceso": p.get("url_proceso"),
            "estado_proceso": p.get("estado_proceso"),
            "modalidad_contratacion": p.get("modalidad_contratacion"),
            "clasificacion_ia": clasif,
        })

    return {
        "ok": True,
        "total": len(resultado),
        "filtros_aplicados": {
            "especie": especie,
            "ciudad": ciudad,
            "limite": limite,
            "solo_relevantes": solo_relevantes,
        },
        "procesos": resultado,
    }
