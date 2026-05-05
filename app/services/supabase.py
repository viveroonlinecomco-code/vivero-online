"""Clientes Supabase:
- `admin`: service_role key, bypassea RLS - solo para operaciones backend.
- `user_client(jwt)`: crea cliente anónimo con JWT del usuario para respetar RLS.
- `decode_jwt(token)`: valida y decodifica el JWT de Supabase.
"""
from __future__ import annotations
from functools import lru_cache
from typing import Any

import jwt
from supabase import create_client, Client

from app.config import get_settings


@lru_cache
def admin() -> Client:
    """Cliente con service_role key - úsalo para operaciones backend."""
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_key)


def user_client(access_token: str) -> Client:
    """Cliente con el JWT del usuario - respeta RLS.

    Úsalo cuando quieras que las políticas RLS se apliquen
    (ej: un viverista solo ve su inventario, no el de otros).
    """
    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_anon_key)
    client.postgrest.auth(access_token)
    return client


def decode_jwt(token: str) -> dict[str, Any]:
    """Valida firma y retorna el payload del JWT de Supabase.

    Lanza jwt.InvalidTokenError si no es válido.
    """
    s = get_settings()
    return jwt.decode(
        token,
        s.supabase_jwt_secret,
        algorithms=["HS256", "ES256", "RS256"],
        audience="authenticated",
    )
