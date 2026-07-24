"""Operaciones admin del marketplace — exclusivo para rol=admin.

Cubre: Comunidad, Suscripciones, Inventario, Viveros, Auditoría.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.deps import UserContext, require_admin
from app.services.supabase import admin as db_admin

router = APIRouter(prefix="/api/admin", tags=["admin-ops"])


# ═══════════ COMUNIDAD ═══════════

@router.get("/comunidad")
async def listar_comunidad(
    q: Optional[str] = None,
    rol: Optional[str] = None,
    user: UserContext = Depends(require_admin),
):
    """Lista todos los usuarios con sus datos, vivero/cliente y suscripción."""
    db = db_admin()
    query = db.table("perfiles").select(
        "id, rol, nombre_display, whatsapp_numero, onboarding_ok, creado_en, vivero_id, cliente_id"
    )
    if rol and rol in ("viverista", "comprador", "admin"):
        query = query.eq("rol", rol)
    result = query.order("creado_en", desc=True).execute()

    comunidad = []
    for p in (result.data or []):
        item = {
            "user_id": p["id"],
            "rol": p["rol"],
            "nombre": p.get("nombre_display") or "Sin nombre",
            "whatsapp": p.get("whatsapp_numero"),
            "onboarding_ok": p.get("onboarding_ok", False),
            "creado_en": str(p.get("creado_en") or ""),
            "vivero_id": p.get("vivero_id"),
            "cliente_id": p.get("cliente_id"),
            "vivero": None,
            "suscripcion": None,
        }

        if p.get("vivero_id"):
            v = db.table("viveros").select(
                "nombre_vivero, ciudad, estado, nit"
            ).eq("vivero_id", p["vivero_id"]).limit(1).execute()
            item["vivero"] = v.data[0] if v.data else None

        sus = db.table("suscripciones").select(
            "suscripcion_id, plan, estado, fecha_proximo_cobro"
        ).eq("user_id", p["id"]).eq("estado", "activa").limit(1).execute()
        item["suscripcion"] = sus.data[0] if sus.data else None

        if q:
            ql = q.lower()
            nm = ql in (item["nombre"] or "").lower()
            vn = ql in str((item.get("vivero") or {}).get("nombre_vivero", "")).lower()
            if not nm and not vn:
                continue

        comunidad.append(item)

    return {"ok": True, "comunidad": comunidad, "total": len(comunidad)}


class EditarPerfilReq(BaseModel):
    nombre_display: Optional[str] = None


@router.patch("/usuario/{user_id}")
async def editar_usuario(
    user_id: str,
    req: EditarPerfilReq,
    user: UserContext = Depends(require_admin),
):
    db = db_admin()
    update = {}
    if req.nombre_display:
        update["nombre_display"] = req.nombre_display
    if update:
        db.table("perfiles").update(update).eq("id", user_id).execute()
    return {"ok": True}


# ═══════════ VIVEROS ═══════════

class CambiarEstadoViveroReq(BaseModel):
    estado: str


@router.patch("/vivero/{vivero_id}/estado")
async def cambiar_estado_vivero(
    vivero_id: int,
    req: CambiarEstadoViveroReq,
    user: UserContext = Depends(require_admin),
):
    if req.estado not in ("activo", "suspendido"):
        raise HTTPException(400, "Estado inválido. Usá: activo | suspendido")
    db = db_admin()
    db.table("viveros").update({"estado": req.estado}).eq("vivero_id", vivero_id).execute()
    return {"ok": True, "vivero_id": vivero_id, "estado": req.estado}


# ═══════════ SUSCRIPCIONES ═══════════

@router.get("/suscripciones")
async def listar_suscripciones(user: UserContext = Depends(require_admin)):
    db = db_admin()
    sus = db.table("suscripciones").select(
        "suscripcion_id, user_id, plan, estado, monto_mensual_cop, "
        "fecha_inicio, fecha_proximo_cobro, fecha_cancelacion, metadata"
    ).order("fecha_inicio", desc=True).execute()

    result = []
    for s in (sus.data or []):
        p = db.table("perfiles").select("nombre_display, rol").eq("id", s["user_id"]).limit(1).execute()
        s["nombre_usuario"] = (p.data[0].get("nombre_display") if p.data else None) or "Desconocido"
        s["rol_usuario"] = (p.data[0].get("rol") if p.data else None) or ""
        result.append(s)

    return {"ok": True, "suscripciones": result}


class ActivarSusReq(BaseModel):
    user_id: str
    dias: int = 30
    nota: Optional[str] = None


@router.post("/suscripcion/activar")
async def activar_suscripcion(req: ActivarSusReq, user: UserContext = Depends(require_admin)):
    db = db_admin()
    existente = db.table("suscripciones").select("suscripcion_id").eq(
        "user_id", req.user_id
    ).eq("estado", "activa").limit(1).execute()
    if existente.data:
        raise HTTPException(400, "El usuario ya tiene una suscripción activa.")
    now = datetime.utcnow()
    r = db.table("suscripciones").insert({
        "user_id": req.user_id,
        "plan": "inteligencia",
        "estado": "activa",
        "monto_mensual_cop": 0,
        "fecha_inicio": now.isoformat(),
        "fecha_proximo_cobro": (now + timedelta(days=req.dias)).isoformat(),
        "metadata": {
            "tipo": "manual_admin",
            "nota": req.nota or "Activado por admin",
            "admin_id": user.user_id,
        },
    }).execute()
    return {"ok": True, "suscripcion_id": r.data[0]["suscripcion_id"]}


class ExtenderSusReq(BaseModel):
    dias: int = 30


@router.patch("/suscripcion/{sus_id}/extender")
async def extender_suscripcion(
    sus_id: int, req: ExtenderSusReq, user: UserContext = Depends(require_admin)
):
    db = db_admin()
    sus = db.table("suscripciones").select(
        "suscripcion_id, fecha_proximo_cobro"
    ).eq("suscripcion_id", sus_id).limit(1).execute()
    if not sus.data:
        raise HTTPException(404, "Suscripción no encontrada")
    fecha_str = str(sus.data[0]["fecha_proximo_cobro"]).replace("+00:00", "").split(".")[0]
    fecha_actual = datetime.fromisoformat(fecha_str)
    nueva = max(fecha_actual, datetime.utcnow()) + timedelta(days=req.dias)
    db.table("suscripciones").update({
        "fecha_proximo_cobro": nueva.isoformat(),
        "estado": "activa",
    }).eq("suscripcion_id", sus_id).execute()
    return {"ok": True, "nueva_fecha_vencimiento": nueva.strftime("%d/%m/%Y")}


@router.patch("/suscripcion/{sus_id}/cancelar")
async def cancelar_suscripcion(sus_id: int, user: UserContext = Depends(require_admin)):
    db = db_admin()
    db.table("suscripciones").update({
        "estado": "cancelada",
        "fecha_cancelacion": datetime.utcnow().isoformat(),
    }).eq("suscripcion_id", sus_id).execute()
    return {"ok": True}


# ═══════════ INVENTARIO ═══════════

@router.get("/inventario")
async def listar_inventario_admin(
    q: Optional[str] = None,
    user: UserContext = Depends(require_admin),
):
    db = db_admin()
    r = db.table("inventario").select(
        "inventario_id, altura_cm, precio_mayorista, stock, estado_planta, foto_ia_url, vivero_id, "
        "plantas(nombre_comun), viveros(nombre_vivero, ciudad)"
    ).order("inventario_id", desc=True).limit(200).execute()
    items = []
    for row in (r.data or []):
        planta = row.get("plantas") or {}
        vivero = row.get("viveros") or {}
        item = {
            "inventario_id": row["inventario_id"],
            "nombre_comun": planta.get("nombre_comun", "Sin nombre"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "ciudad": vivero.get("ciudad"),
            "vivero_id": row["vivero_id"],
            "precio_mayorista": float(row.get("precio_mayorista") or 0),
            "stock": row.get("stock") or 0,
            "altura_cm": row.get("altura_cm") or 0,
            "estado_planta": row.get("estado_planta"),
            "foto_ia_url": row.get("foto_ia_url"),
        }
        if q and q.lower() not in (item["nombre_comun"] or "").lower() \
               and q.lower() not in (item["nombre_vivero"] or "").lower():
            continue
        items.append(item)

    return {"ok": True, "items": items, "total": len(items)}


class CambiarEstadoItemReq(BaseModel):
    estado_planta: str


@router.patch("/inventario/{inv_id}/estado")
async def cambiar_estado_item(
    inv_id: int, req: CambiarEstadoItemReq, user: UserContext = Depends(require_admin)
):
    validos = ("disponible", "agotado", "descontinuado")
    if req.estado_planta not in validos:
        raise HTTPException(400, f"Estado inválido. Usá: {' | '.join(validos)}")
    db = db_admin()
    db.table("inventario").update({"estado_planta": req.estado_planta}).eq("inventario_id", inv_id).execute()
    return {"ok": True, "inventario_id": inv_id, "estado_planta": req.estado_planta}


# ═══════════ AUDITORÍA ═══════════

@router.get("/auditoria")
async def listar_auditoria(user: UserContext = Depends(require_admin)):
    db = db_admin()
    logs = db.table("log_ia").select("*").order("creado_en", desc=True).limit(100).execute()
    return {"ok": True, "logs": logs.data or []}
