"""Transacciones del usuario autenticado.

- Listado: ventas si viverista, compras si comprador, todas si admin.
- Detalle: una transacción específica, con autorización por rol.
"""
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
        # Filtro en BD (no en Python): inventario!inner fuerza INNER JOIN,
        # luego .eq("inventario.vivero_id", X) filtra por la columna joinada.
        # Esto evita traer todas las transacciones de la plataforma sólo para descartarlas.
        resp = db.table("transacciones_b2b").select(
            "transaccion_id, cantidad, precio_unitario, precio_total, estado, "
            "fecha_transaccion, inventario_id, "
            "inventario!inner(vivero_id, plantas(nombre_comun)), "
            "clientes(nombre_empresa, nombre_representante)"
        ).eq("inventario.vivero_id", user.vivero_id).execute()
        # Defensa en profundidad: si el filtro de BD fallara silenciosamente
        # (cambio de schema, bug de versión del cliente, etc.), este chequeo
        # bloquea cualquier fila que se cuele con vivero_id distinto.
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


@router.get("/{transaccion_id}", response_model=TransaccionOut)
async def detalle_transaccion(
    transaccion_id: int,
    user: UserContext = Depends(require_user),
):
    """Detalle de una transacción específica con autorización por rol.

    - Comprador: solo si fue el cliente que la generó (cliente_id matchea).
    - Viverista: solo si fue el vendedor (vivero_id del inventario matchea).
    - Admin: cualquier transacción.

    Returns 404 si la transacción no existe, 403 si el usuario no tiene acceso.
    """
    db = admin()

    # Query con todos los joins para autorización + display
    resp = db.table("transacciones_b2b").select(
        "transaccion_id, cantidad, precio_unitario, precio_total, estado, "
        "fecha_transaccion, cliente_id, "
        "inventario(vivero_id, plantas(nombre_comun, nombre_cientifico), viveros(nombre_vivero)), "
        "clientes(nombre_empresa, nombre_representante)"
    ).eq("transaccion_id", transaccion_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Transacción no encontrada")

    r = resp.data[0]
    inv = r.get("inventario") or {}
    plantas = inv.get("plantas") or {}
    viveros = inv.get("viveros") or {}
    cliente = r.get("clientes") or {}

    # Autorización por rol
    if user.rol == "comprador":
        if not user.cliente_id or r.get("cliente_id") != user.cliente_id:
            raise HTTPException(403, detail="No tienes acceso a esta transacción")
    elif user.rol == "viverista":
        if not user.vivero_id or inv.get("vivero_id") != user.vivero_id:
            raise HTTPException(403, detail="No tienes acceso a esta transacción")
    elif user.rol != "admin":
        raise HTTPException(403, detail="Rol no autorizado")

    return TransaccionOut(
        transaccion_id=r["transaccion_id"],
        cantidad=r["cantidad"],
        precio_unitario=float(r["precio_unitario"]),
        precio_total=float(r["precio_total"]),
        estado=r["estado"],
        fecha_transaccion=str(r.get("fecha_transaccion") or ""),
        nombre_planta=plantas.get("nombre_comun"),
        nombre_vivero=viveros.get("nombre_vivero"),
        comprador=cliente.get("nombre_empresa") or cliente.get("nombre_representante"),
    )
