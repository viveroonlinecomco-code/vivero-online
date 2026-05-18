"""Clientes Supabase:
- `admin`: service_role key, bypassea RLS - solo para operaciones backend.
- `user_client(jwt)`: crea cliente anónimo con JWT del usuario para respetar RLS.
- `decode_jwt(token)`: valida y decodifica el JWT de Supabase.
"""
from __future__ import annotations
from typing import Any

import jwt
from supabase import create_client, Client

from app.config import get_settings


def admin() -> Client:
    """Cliente fresh con service_role key — úsalo para operaciones backend.

    IMPORTANTE: NO se cachea con lru_cache. Si dos llamadas comparten el
    mismo Client y una llama a `client.auth.verify_otp(...)`, ese Client
    queda contaminado con el JWT del usuario, y las queries posteriores
    se evalúan como `authenticated` (no `service_role`) → RLS las rechaza
    con error 42501.

    Cada llamada crea un Client nuevo: barato (no abre conexiones hasta
    la primera query) y aísla state entre invocaciones del Lambda.
    """
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
        algorithms=["HS256"],
        audience="authenticated",
    )
