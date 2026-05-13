"""Dependencias FastAPI para autenticación y autorización.

Uso:
    @router.get("/catalogo")
    async def catalogo(user: UserContext = Depends(require_user)):
        ...

Nota sobre el flujo de tokens:
    El JWT se emite en `verify_otp` ANTES de que exista el perfil del usuario
    (el onboarding viene después). Por eso un token recién emitido puede
    no tener `rol`, `vivero_id` o `cliente_id` en sus claims.

    Si eso pasa, `require_user` enriquece el UserContext con datos de la
    tabla `perfiles` para no rechazar prematuramente requests legítimos.
    La siguiente vez que el cliente refresque el token, ya vendrán incluidos
    desde `app_metadata` (que se actualiza en `complete_onboarding`).
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
    """Contexto del usuario autenticado, extraído del JWT (+DB fallback)."""
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

    Si el JWT no trae `rol` (token pre-onboarding o hook personalizado que
    no lo expone en top-level), enriquece el UserContext con datos de la
    tabla `perfiles`. La query DB solo corre cuando hace falta.
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

    # Extraer claims: busca tanto en top-level (custom hook) como en app_metadata
    rol = payload.get("rol") or app_meta.get("rol")
    vivero_id = payload.get("vivero_id") or app_meta.get("vivero_id")
    cliente_id = payload.get("cliente_id") or app_meta.get("cliente_id")
    whatsapp = (
        payload.get("whatsapp_numero")
        or app_meta.get("whatsapp_numero")
        or user_meta.get("whatsapp_numero")
        or payload.get("phone", "")
    )

    # Fallback DB si el JWT no expone rol — pasa cuando el token fue emitido
    # antes del onboarding (verify_otp → token, después → complete_onboarding).
    # Esto evita el 403 prematuro hasta que el cliente refresque el token.
    if not rol:
        try:
            from app.services.supabase import admin
            db = admin()
            resp = db.table("perfiles").select(
                "rol, vivero_id, cliente_id, whatsapp_numero"
            ).eq("id", user_id).limit(1).execute()
            if resp.data:
                p = resp.data[0]
                rol = p.get("rol")
                if vivero_id is None:
                    vivero_id = p.get("vivero_id")
                if cliente_id is None:
                    cliente_id = p.get("cliente_id")
                if not whatsapp:
                    whatsapp = p.get("whatsapp_numero") or ""
                if rol:
                    logger.info(
                        "UserContext enriquecido desde DB para user_id=%s "
                        "(JWT sin rol; DB rol=%s, vivero_id=%s, cliente_id=%s)",
                        user_id, rol, vivero_id, cliente_id,
                    )
        except Exception as e:
            logger.warning(
                "No se pudo enriquecer UserContext desde DB para %s: %s",
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
