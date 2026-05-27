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

def _extraer_user_id(user) -> str | None:
    """Extrae el user_id del UserContext sea cual sea el nombre del campo."""
    for attr in ("id", "user_id", "sub", "uid"):
        val = getattr(user, attr, None)
        if val:
            return str(val)
    return None


def _verificar_suscripcion(user_id, plan: str = "inteligencia") -> bool:
    """Verifica si el user tiene suscripción activa del plan dado (o superior).

    Defensivo: cualquier error de query → devuelve False (no crashea).
    """
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
        # Filtramos en Python para evitar incompatibilidades con .in_()
        planes_validos = {plan, "pro"}
        for r in rows:
            if r.get("plan") in planes_validos:
                return True
        return False
    except Exception as e:
        import logging
        logging.error(f"Error en _verificar_suscripcion: {e!r}")
        return False

# ─────────────────── ENDPOINT: MERCADO ───────────────────

@router.get("/mercado")
async def get_mercado(user: UserContext = Depends(require_user)):
    """Dashboard de mercado: flywheel, crecimiento, demanda municipio, top especies.

    Gateado por suscripción Inteligencia activa (admin bypassa).
    """
    user_id = _extraer_user_id(user)
    # Admin puede ver siempre (para auditoría / testing)
    if user.rol != "admin":
        if not _verificar_suscripcion(user_id, plan="inteligencia"):
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


# ─────────────────── ENDPOINT: SECOP ───────────────────

@router.get("/secop")
async def get_secop(
    especie: str | None = None,
    municipio: str | None = None,
    limite: int = 50,
    incluir_no_relevantes: bool = False,
    user: UserContext = Depends(require_user),
):
    """Feed de procesos SECOP clasificados.

    Por default solo devuelve los marcados como relevantes por la IA.
    Filtros opcionales por especie mencionada y municipio.
    Gateado por suscripción Inteligencia activa (admin bypassa).
    """
     user_id = _extraer_user_id(user)
    # Admin puede ver siempre
    if user.rol != "admin":
        if not _verificar_suscripcion(user_id, plan="inteligencia"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "subscription_required",
                    "plan_requerido": "inteligencia",
                    "mensaje": (
                        "Necesitás el plan Inteligencia para acceder al "
                        "feed SECOP. Suscribite desde /mi-cuenta/suscripcion."
                    ),
                },
            )

    # Limitar para evitar abuso
    limite = max(1, min(limite, 200))

    db = admin()

    # Query: secop_procesos LEFT JOIN secop_clasificacion
    # Trae todos los procesos + su clasificación si existe
    query = (
        db.table("secop_procesos")
        .select(
            "*, secop_clasificacion!left(especies_mencionadas, cantidad_estimada, "
            "altura_estimada_cm, tipo_proyecto, es_relevante, confianza, modelo_usado)"
        )
        .order("fecha_de_publicacion_del", desc=True)
        .limit(limite)
    )

    if municipio:
        query = query.ilike("municipio_entidad", f"%{municipio}%")

    resp = query.execute()
    procesos = resp.data or []

    # Filtrado en Python (Supabase REST no soporta filtros en relaciones nested fácil)
    resultado = []
    for p in procesos:
        clasif = p.get("secop_clasificacion") or []
        clasif = clasif[0] if isinstance(clasif, list) and clasif else (clasif if isinstance(clasif, dict) else None)

        es_relevante = clasif.get("es_relevante") if clasif else None

        # Filtro por relevancia (default: solo relevantes)
        if not incluir_no_relevantes and es_relevante is not True:
            continue

        # Filtro por especie mencionada
        if especie:
            especies = (clasif or {}).get("especies_mencionadas") or []
            especies_lower = [str(e).lower() for e in especies]
            if not any(especie.lower() in e for e in especies_lower):
                continue

        # Aplanar el resultado
        resultado.append({
            "id_del_proceso": p.get("id_del_proceso"),
            "entidad": p.get("entidad"),
            "objeto_del_contrato": p.get("descripci_n_del_procedimiento"),
            "cuantia_cop": p.get("precio_base"),
            "departamento_entidad": p.get("departamento_entidad"),
            "municipio_entidad": p.get("municipio_entidad"),
            "fecha_publicacion": p.get("fecha_de_publicacion_del"),
            "url_proceso": p.get("urlproceso"),
            "estado_del_procedimiento": p.get("estado_del_procedimiento"),
            "modalidad_de_contratacion": p.get("modalidad_de_contratacion"),
            "clasificacion_ia": clasif,
        })

    return {
        "ok": True,
        "total": len(resultado),
        "filtros_aplicados": {
            "especie": especie,
            "municipio": municipio,
            "limite": limite,
            "incluir_no_relevantes": incluir_no_relevantes,
        },
        "procesos": resultado,
    }
