"""Dependencias FastAPI para autenticación y autorización.

Uso:
    @router.get("/catalogo")
    async def catalogo(user: UserContext = Depends(require_user)):
        ...
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.supabase import decode_jwt


@dataclass
class UserContext:
    """Contexto del usuario autenticado, extraído del JWT."""
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
    """Requiere un JWT válido. Lanza 401 si no hay o si es inválido."""
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

    return UserContext(
        user_id=payload["sub"],
        whatsapp=(
               payload.get("whatsapp_numero")
               or (payload.get("app_metadata") or {}).get("whatsapp_numero")
               or (payload.get("user_metadata") or {}).get("whatsapp_numero")
               or payload.get("phone", "")
           ),
        rol=payload.get("rol"),
        vivero_id=payload.get("vivero_id"),
        cliente_id=payload.get("cliente_id"),
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
