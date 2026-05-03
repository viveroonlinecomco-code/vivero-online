"""Transacciones del usuario autenticado (ventas si viverista, compras si comprador)."""
from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import UserContext, require_user
from app.schemas.transactions import TransaccionesResponse, TransaccionOut
from app.services.supabase import admin


router = APIRouter(prefix="/api/transacciones", tags=["transacciones"])


@router.get("", response_model=TransaccionesResponse)
async def listar_transacciones(user: UserContext = Depends(require_user)):
    """Lista transacciones del usuario según su rol:
    - Viverista → ventas (transacciones donde el inventario es de su vivero)
    - Comprador → compras (transacciones donde el cliente_id es suyo)
    - Admin → todas
    """
    db = admin()

    if user.rol == "viverista":
        if not user.vivero_id:
            raise HTTPException(400, detail="Perfil sin vivero asociado")
        # Joins para traer info de la planta y el comprador
        resp = db.table("transacciones_b2b").select(
            "transaccion_id, cantidad, precio_unitario, precio_total, estado, "
            "fecha_transaccion, inventario_id, "
            "inventario(vivero_id, plantas(nombre_comun)), "
            "clientes(nombre_empresa, nombre_representante)"
        ).execute()
        items = [
            TransaccionOut(
                transaccion_id=r["transaccion_id"],
                cantidad=r["cantidad"],
                precio_unitario=float(r["precio_unitario"]),
                precio_total=float(r["precio_total"]),
                estado=r["estado"],
                fecha_transaccion=str(r.get("fecha_transaccion") or ""),
                nombre_planta=(r.get("inventario") or {}).get("plantas", {}).get("nombre_comun"),
                comprador=(r.get("clientes") or {}).get("nombre_empresa"),
            )
            for r in (resp.data or [])
            if (r.get("inventario") or {}).get("vivero_id") == user.vivero_id
        ]
        return TransaccionesResponse(ok=True, items=items)

    if user.rol == "comprador":
        if not user.cliente_id:
            raise HTTPException(400, detail="Perfil sin cliente asociado")
        resp = db.table("transacciones_b2b").select(
            "transaccion_id, cantidad, precio_unitario, precio_total, estado, "
            "fecha_transaccion, "
            "inventario(plantas(nombre_comun), viveros(nombre_vivero))"
        ).eq("cliente_id", user.cliente_id).execute()
        items = [
            TransaccionOut(
                transaccion_id=r["transaccion_id"],
                cantidad=r["cantidad"],
                precio_unitario=float(r["precio_unitario"]),
                precio_total=float(r["precio_total"]),
                estado=r["estado"],
                fecha_transaccion=str(r.get("fecha_transaccion") or ""),
                nombre_planta=((r.get("inventario") or {}).get("plantas") or {}).get("nombre_comun"),
                nombre_vivero=((r.get("inventario") or {}).get("viveros") or {}).get("nombre_vivero"),
            )
            for r in (resp.data or [])
        ]
        return TransaccionesResponse(ok=True, items=items)

    # Admin: todas
    if user.rol == "admin":
        resp = db.table("transacciones_b2b").select("*").limit(200).execute()
        items = [
            TransaccionOut(
                transaccion_id=r["transaccion_id"],
                cantidad=r["cantidad"],
                precio_unitario=float(r["precio_unitario"]),
                precio_total=float(r["precio_total"]),
                estado=r["estado"],
                fecha_transaccion=str(r.get("fecha_transaccion") or ""),
            )
            for r in (resp.data or [])
        ]
        return TransaccionesResponse(ok=True, items=items)

    raise HTTPException(403, detail="Rol no reconocido")
