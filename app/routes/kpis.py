"""Dashboard KPI (solo admin)."""
from fastapi import APIRouter, Depends

from app.auth.deps import UserContext, require_admin
from app.schemas.transactions import KpisResponse
from app.services.supabase import admin


router = APIRouter(prefix="/api/kpis", tags=["admin"])


@router.get("", response_model=KpisResponse)
async def get_kpis(user: UserContext = Depends(require_admin)):
    """Lee las 3 vistas del Data Flywheel."""
    db = admin()

    k_resp = db.table("v_flywheel_kpis").select("*").execute()
    c_resp = db.table("v_crecimiento_semanal").select("*").execute()
    t_resp = db.table("v_top_viveristas").select(
        "vivero_id, nombre_vivero, ciudad, ventas_90d, gmv_90d_cop, ticket_promedio_cop"
    ).limit(10).execute()

    k = (k_resp.data or [{}])[0]

    return KpisResponse(
        gmv_total_cop=float(k.get("gmv_total_cop", 0) or 0),
        comision_plataforma_cop=float(k.get("comision_plataforma_cop", 0) or 0),
        transacciones_validas=k.get("transacciones_validas", 0),
        ticket_promedio_cop=float(k.get("ticket_promedio_cop", 0) or 0),
        compradores_activos=k.get("compradores_activos", 0),
        viveristas_activos=k.get("viveristas_activos", 0),
        items_disponibles=k.get("items_disponibles", 0),
        viveros_registrados=k.get("viveros_registrados", 0),
        crecimiento_semanal=c_resp.data or [],
        top_viveristas=t_resp.data or [],
    )
