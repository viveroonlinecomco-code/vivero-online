"""Endpoints admin para leer/editar configuracion_global.

Solo accesibles por usuarios con rol=admin (via require_admin).
Los cambios se aplican en tiempo real (invalidan caché del helper).
Toda modificación queda registrada en admin_audit_log.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.deps import UserContext, require_admin
from app.services.supabase import admin as db_admin
from app.services.config_global import invalidate_cache

router = APIRouter(prefix="/api/admin/config", tags=["admin-config"])


# ═══════════ MODELOS ═══════════

class UpdateConfigReq(BaseModel):
    valor: str
    descripcion: Optional[str] = None


class CreateConfigReq(BaseModel):
    clave: str
    valor: str
    tipo_dato: str = "string"  # string | number | boolean | json
    descripcion: Optional[str] = None


# ═══════════ HELPERS ═══════════

def _registrar_auditoria(
    admin_id: str,
    accion: str,
    clave: str,
    valor_anterior: Optional[dict] = None,
    valor_nuevo: Optional[dict] = None,
    descripcion: Optional[str] = None,
) -> None:
    """Registra una acción admin en admin_audit_log.

    Best-effort: si falla, no bloquea la operación principal.
    """
    try:
        db = db_admin()
        db.table("admin_audit_log").insert({
            "admin_id": admin_id,
            "accion": accion,
            "entidad": "configuracion_global",
            "entidad_id": clave,
            "valor_anterior": valor_anterior,
            "valor_nuevo": valor_nuevo,
            "descripcion": descripcion,
        }).execute()
    except Exception as e:
        # No propagar — auditoría es best-effort
        import logging
        logging.getLogger(__name__).warning(
            f"No se pudo registrar auditoría de {accion} sobre {clave}: {e}"
        )


# ═══════════ ENDPOINTS ═══════════

@router.get("")
async def listar_configuracion(
    prefix: Optional[str] = None,
    user: UserContext = Depends(require_admin),
):
    """Lista todas las claves de configuración con sus valores.

    Query params:
        prefix: filtrar solo claves que empiezan con este prefix
                (ej: 'descuento_' → solo claves de descuentos)

    Returns:
        {
            "ok": true,
            "config": [
                {clave, valor, tipo_dato, descripcion, actualizado_en, actualizado_por},
                ...
            ],
            "total": N
        }
    """
    db = db_admin()
    query = db.table("configuracion_global").select(
        "clave, valor, tipo_dato, descripcion, actualizado_en, actualizado_por"
    )

    result = query.order("clave").execute()
    items = result.data or []

    if prefix:
        prefix_lower = prefix.lower().strip()
        items = [it for it in items if it["clave"].startswith(prefix_lower)]

    return {"ok": True, "config": items, "total": len(items)}


@router.get("/{clave}")
async def obtener_configuracion(
    clave: str,
    user: UserContext = Depends(require_admin),
):
    """Obtiene el valor de una clave específica."""
    db = db_admin()
    resp = db.table("configuracion_global").select(
        "clave, valor, tipo_dato, descripcion, actualizado_en, actualizado_por"
    ).eq("clave", clave).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, f"Clave '{clave}' no existe en configuracion_global")

    return {"ok": True, "config": resp.data[0]}


@router.patch("/{clave}")
async def actualizar_configuracion(
    clave: str,
    req: UpdateConfigReq,
    user: UserContext = Depends(require_admin),
):
    """Actualiza el valor de una clave existente.

    - Valida que la clave exista (404 si no)
    - Registra en admin_audit_log (valor anterior + nuevo)
    - Invalida la caché del helper get_config para aplicar cambio inmediato
    - Actualiza actualizado_por con el user_id del admin

    NO permite crear claves nuevas (para eso usar POST).
    """
    db = db_admin()

    # 1. Verificar que la clave existe y capturar valor anterior
    resp = db.table("configuracion_global").select(
        "clave, valor, tipo_dato, descripcion"
    ).eq("clave", clave).limit(1).execute()

    if not resp.data:
        raise HTTPException(
            404,
            f"Clave '{clave}' no existe. Para crear una clave nueva usá POST /api/admin/config"
        )

    valor_anterior = resp.data[0]

    # 2. Validar el nuevo valor según tipo_dato
    tipo_dato = valor_anterior["tipo_dato"]
    valor_nuevo_str = req.valor.strip()

    try:
        if tipo_dato == "number":
            # Verificar que sea número válido
            float(valor_nuevo_str)
        elif tipo_dato == "boolean":
            if valor_nuevo_str.lower() not in ("true", "false", "1", "0", "yes", "no"):
                raise ValueError("Debe ser 'true' o 'false'")
        elif tipo_dato == "json":
            import json
            json.loads(valor_nuevo_str)
    except (ValueError, Exception) as e:
        raise HTTPException(
            400,
            f"Valor inválido para tipo {tipo_dato}: {e}"
        )

    # 3. Actualizar en BD
    update_data = {
        "valor": valor_nuevo_str,
        "actualizado_por": user.user_id,
    }
    if req.descripcion is not None:
        update_data["descripcion"] = req.descripcion

    db.table("configuracion_global").update(update_data).eq("clave", clave).execute()

    # 4. Invalidar caché del helper (cambio se aplica INMEDIATO)
    invalidate_cache(clave)

    # 5. Registrar en auditoría (best-effort)
    _registrar_auditoria(
        admin_id=user.user_id,
        accion="update_config",
        clave=clave,
        valor_anterior={"valor": valor_anterior["valor"], "descripcion": valor_anterior.get("descripcion")},
        valor_nuevo={"valor": valor_nuevo_str, "descripcion": req.descripcion or valor_anterior.get("descripcion")},
        descripcion=f"Actualización de configuración '{clave}'",
    )

    return {
        "ok": True,
        "clave": clave,
        "valor_anterior": valor_anterior["valor"],
        "valor_nuevo": valor_nuevo_str,
        "aplicado": "inmediato (caché invalidada)",
    }


@router.post("")
async def crear_configuracion(
    req: CreateConfigReq,
    user: UserContext = Depends(require_admin),
):
    """Crea una nueva clave de configuración.

    Falla con 409 si la clave ya existe (para editar usar PATCH).
    """
    tipos_validos = ("string", "number", "boolean", "json")
    if req.tipo_dato not in tipos_validos:
        raise HTTPException(
            400,
            f"tipo_dato inválido. Usar: {' | '.join(tipos_validos)}"
        )

    clave = req.clave.strip().lower()
    if not clave:
        raise HTTPException(400, "clave no puede estar vacía")

    db = db_admin()

    # Verificar que no exista
    existing = db.table("configuracion_global").select("clave").eq("clave", clave).limit(1).execute()
    if existing.data:
        raise HTTPException(409, f"La clave '{clave}' ya existe. Para actualizar usar PATCH.")

    # Validar valor según tipo
    valor_str = req.valor.strip()
    try:
        if req.tipo_dato == "number":
            float(valor_str)
        elif req.tipo_dato == "boolean":
            if valor_str.lower() not in ("true", "false", "1", "0", "yes", "no"):
                raise ValueError("Debe ser 'true' o 'false'")
        elif req.tipo_dato == "json":
            import json
            json.loads(valor_str)
    except (ValueError, Exception) as e:
        raise HTTPException(400, f"Valor inválido para tipo {req.tipo_dato}: {e}")

    # Crear
    db.table("configuracion_global").insert({
        "clave": clave,
        "valor": valor_str,
        "tipo_dato": req.tipo_dato,
        "descripcion": req.descripcion,
        "actualizado_por": user.user_id,
    }).execute()

    # Registrar auditoría
    _registrar_auditoria(
        admin_id=user.user_id,
        accion="create_config",
        clave=clave,
        valor_nuevo={"valor": valor_str, "tipo_dato": req.tipo_dato, "descripcion": req.descripcion},
        descripcion=f"Nueva clave de configuración '{clave}'",
    )

    return {"ok": True, "clave": clave, "creado": True}


@router.delete("/{clave}")
async def eliminar_configuracion(
    clave: str,
    user: UserContext = Depends(require_admin),
):
    """Elimina una clave de configuración.

    ⚠️ USAR CON CUIDADO: si el código lee esta clave con get_config, quedará
    con el valor por defecto. Preferí actualizar el valor antes de eliminar.
    """
    db = db_admin()

    # Capturar valor anterior para auditoría
    resp = db.table("configuracion_global").select("*").eq("clave", clave).limit(1).execute()
    if not resp.data:
        raise HTTPException(404, f"Clave '{clave}' no existe")

    valor_anterior = resp.data[0]

    # Eliminar
    db.table("configuracion_global").delete().eq("clave", clave).execute()

    # Invalidar caché
    invalidate_cache(clave)

    # Auditoría
    _registrar_auditoria(
        admin_id=user.user_id,
        accion="delete_config",
        clave=clave,
        valor_anterior={"valor": valor_anterior["valor"], "tipo_dato": valor_anterior.get("tipo_dato")},
        descripcion=f"Eliminación de configuración '{clave}'",
    )

    return {"ok": True, "clave": clave, "eliminado": True}


# ═══════════ AUDITORÍA DE CONFIGURACIÓN ═══════════

@router.get("/_audit/history")
async def historial_cambios(
    clave: Optional[str] = None,
    limit: int = 50,
    user: UserContext = Depends(require_admin),
):
    """Retorna el historial de cambios sobre configuración.

    Query params:
        clave: filtrar solo cambios sobre esta clave específica
        limit: máximo de registros (default 50, máx 200)

    Returns:
        Lista de acciones ordenadas por fecha_hora DESC.
    """
    limit = min(max(limit, 1), 200)

    db = db_admin()
    query = db.table("admin_audit_log").select(
        "log_id, fecha_hora, admin_id, accion, entidad_id, valor_anterior, valor_nuevo, descripcion"
    ).eq("entidad", "configuracion_global")

    if clave:
        query = query.eq("entidad_id", clave)

    result = query.order("fecha_hora", desc=True).limit(limit).execute()

    return {
        "ok": True,
        "historial": result.data or [],
        "total": len(result.data or []),
    }
