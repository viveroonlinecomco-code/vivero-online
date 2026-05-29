"""Dependencias FastAPI para autenticación y autorización.

Uso:
    @router.get("/catalogo")
    async def catalogo(user: UserContext = Depends(require_user)):
        ...

Filosofía: "DB > JWT" para rol y datos de perfil
    El JWT lleva los claims del momento en que se emitió, pero el perfil
    del usuario en BD puede cambiar después (ej. admin asciende a un
    usuario, el usuario completa onboarding, etc.). Si confiamos solo en
    el JWT, quedaríamos atados a información desactualizada hasta que el
    cliente refresque el token.

    Por eso `require_user` SIEMPRE consulta la tabla `perfiles` y usa esos
    valores cuando existen. El JWT funciona como fallback si la query
    falla. Mismo patrón que `/api/auth/me`.

    Costo: 1 query extra a Supabase por request autenticado (~20-50ms
    sobre `perfiles.id` que es primary key indexada).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.supabase import decode_jwt


logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    """Contexto del usuario autenticado, con override desde DB."""
    user_id: str
    whatsapp: str
    rol: Optional[str]
    vivero_id: Optional[int]
    cliente_id: Optional[int]
    access_token: str  # para crear cliente Supabase con RLS del usuario


bearer = HTTPBearer(auto_error=False)


async def require_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> UserContext:
    """Requiere un JWT válido. Lanza 401 si no hay o si es inválido.

    Después de validar el JWT, SIEMPRE consulta la tabla `perfiles` y
    sobreescribe los valores del JWT con los de la BD (cuando existen).
    Así el rol refleja el estado actual del usuario, no el que tenía
    cuando se emitió el token.
    """
    if not creds or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el token de autenticación",
        )
    token = creds.credentials
    try:
        payload = decode_jwt(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {e}")

    user_id = payload["sub"]
    app_meta = payload.get("app_metadata") or {}
    user_meta = payload.get("user_metadata") or {}

    # 1. Extraer claims del JWT como base (fallback si la BD falla)
    rol = payload.get("rol") or app_meta.get("rol")
    vivero_id = payload.get("vivero_id") or app_meta.get("vivero_id")
    cliente_id = payload.get("cliente_id") or app_meta.get("cliente_id")
    whatsapp = (
        payload.get("whatsapp_numero")
        or app_meta.get("whatsapp_numero")
        or user_meta.get("whatsapp_numero")
        or payload.get("phone", "")
    )

    # 2. Consultar BD y sobreescribir si hay datos. La BD es source-of-truth;
    # el JWT queda como fallback por si la query falla (proyecto pausado, red).
    try:
        from app.services.supabase import admin
        db = admin()
        resp = db.table("perfiles").select(
            "rol, vivero_id, cliente_id, whatsapp_numero"
        ).eq("id", user_id).limit(1).execute()
        if resp.data:
            p = resp.data[0]
            db_rol = p.get("rol")
            db_vivero_id = p.get("vivero_id")
            db_cliente_id = p.get("cliente_id")
            db_whatsapp = p.get("whatsapp_numero")

            # Log si hay discrepancia (útil para detectar JWTs desactualizados)
            if rol and db_rol and rol != db_rol:
                logger.info(
                    "Rol del JWT (%s) difiere de DB (%s) para user_id=%s — usando DB",
                    rol, db_rol, user_id,
                )

            # DB > JWT: solo sobreescribir si la BD tiene valor
            if db_rol:
                rol = db_rol
            if db_vivero_id is not None:
                vivero_id = db_vivero_id
            if db_cliente_id is not None:
                cliente_id = db_cliente_id
            if db_whatsapp:
                whatsapp = db_whatsapp
    except Exception as e:
        logger.warning(
            "No se pudo enriquecer UserContext desde DB para %s (usando JWT): %s",
            user_id, e,
        )

    return UserContext(
        user_id=user_id,
        whatsapp=whatsapp,
        rol=rol,
        vivero_id=vivero_id,
        cliente_id=cliente_id,
        access_token=token,
    )


async def require_viverista(user: UserContext = Depends(require_user)) -> UserContext:
    if user.rol not in ("viverista", "admin"):
        raise HTTPException(403, detail="Solo viveristas pueden acceder")
    return user


async def require_comprador(user: UserContext = Depends(require_user)) -> UserContext:
    if user.rol not in ("comprador", "admin"):
        raise HTTPException(403, detail="Solo compradores pueden acceder")
    return user


async def require_admin(user: UserContext = Depends(require_user)) -> UserContext:
    if user.rol != "admin":
        raise HTTPException(403, detail="Solo admin")
    return user
