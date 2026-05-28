"""Flujo de autenticación Email OTP → Supabase Auth.

Estrategia (ahora 100% Supabase, sin Twilio):
1. Supabase Auth envía el código OTP por email (vía SMTP custom: Gmail).
2. Supabase valida el código y devuelve la sesión (access + refresh token).
3. El JWT pasa por custom_access_token_hook que inyecta rol/whatsapp desde
   la tabla `perfiles` (si el perfil existe).
4. El WhatsApp se captura en el onboarding como dato de contacto del negocio.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException, status

from app.config import get_settings
from app.schemas.auth import OnboardingRequest
from app.services.supabase import admin


logger = logging.getLogger(__name__)


# ─────────────────── SEND OTP ───────────────────

async def send_otp(email: str) -> dict:
    """Envía código OTP por email vía Supabase Auth (SMTP custom)."""
    s = get_settings()
    email = email.strip().lower()

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{s.supabase_url}/auth/v1/otp",
                headers={
                    "apikey": s.supabase_anon_key,
                    "Content-Type": "application/json",
                },
                json={"email": email, "create_user": True},
            )
    except Exception as e:
        logger.error("Error enviando OTP por email: %r", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo enviar el código. Intentá de nuevo en un momento.",
        )

    if resp.status_code == 429:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos. Esperá unos minutos e intentá de nuevo.",
        )
    if resp.status_code >= 400:
        logger.error("Supabase OTP send falló: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo enviar el código. Verificá que el email sea correcto.",
        )

    return {
        "ok": True,
        "message": "Te enviamos un código a tu email",
        "delivered_via": "email",
    }


# ─────────────────── VERIFY OTP ───────────────────

async def verify_otp(email: str, code: str) -> dict:
    """Valida el código OTP con Supabase y devuelve la sesión."""
    s = get_settings()
    email = email.strip().lower()
    code = code.strip()

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{s.supabase_url}/auth/v1/verify",
                headers={
                    "apikey": s.supabase_anon_key,
                    "Content-Type": "application/json",
                },
                json={"type": "email", "email": email, "token": code},
            )
    except Exception as e:
        logger.error("Error verificando OTP: %r", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo verificar el código. Intentá de nuevo.",
        )

    if resp.status_code >= 400:
        logger.warning("Supabase OTP verify rechazado: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Código incorrecto o expirado",
        )

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    user = data.get("user") or {}
    user_id = user.get("id")

    if not access_token or not user_id:
        logger.error("Respuesta de verify sin sesión: %s", str(data)[:300])
        raise HTTPException(500, detail="No se pudo crear la sesión")

    # ¿Ya tiene perfil completo?
    client_db = admin()
    profile_resp = client_db.table("perfiles").select(
        "rol, vivero_id, cliente_id, onboarding_ok, whatsapp_numero"
    ).eq("id", user_id).execute()
    rows = profile_resp.data or []

    needs_onboarding = True
    rol = None
    whatsapp = None
    if rows:
        p = rows[0]
        if p.get("onboarding_ok"):
            needs_onboarding = False
            rol = p.get("rol")
            whatsapp = p.get("whatsapp_numero")

    return {
        "ok": True,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": user_id,
        "email": email,
        "whatsapp": whatsapp,
        "needs_onboarding": needs_onboarding,
        "rol": rol,
    }


# ─────────────────── APP_METADATA REFRESH ───────────────────

def _refresh_app_metadata(client, user_id: str, fields: dict) -> None:
    """Mergea `fields` en el app_metadata del usuario en Supabase Auth."""
    try:
        current = client.auth.admin.get_user_by_id(user_id)
        existing = {}
        if current and getattr(current, "user", None):
            existing = current.user.app_metadata or {}
        merged = {**existing, **fields}
        client.auth.admin.update_user_by_id(user_id, {"app_metadata": merged})
        logger.info("app_metadata actualizado para %s: rol=%s", user_id, fields.get("rol"))
    except Exception as e:
        logger.warning(
            "No se pudo actualizar app_metadata para %s: %s. "
            "Fallback DB en /api/auth/me sigue funcionando.",
            user_id, e,
        )


# ─────────────────── ONBOARDING ───────────────────

async def complete_onboarding(user_id: str, data: OnboardingRequest) -> dict:
    """Crea perfil + (viveros | clientes) según el rol elegido.

    El WhatsApp ahora viene en el body (data.whatsapp) como dato de contacto.
    """
    client = admin()
    whatsapp = data.whatsapp  # ← viene del onboarding ahora, no del token

    # ────────── VIVERISTA ──────────
    if data.rol == "viverista":
        if not data.nombre_vivero:
            raise HTTPException(400, detail="nombre_vivero requerido")

        vivero_resp = client.table("viveros").insert({
            "nombre_vivero": data.nombre_vivero,
            "propietario": data.nombre,
            "ciudad": data.municipio,
            "departamento": "Cundinamarca",
            "telefono": whatsapp,
            "whatsapp_numero": whatsapp,
            "nit": data.nit,
            "habeas_data": data.habeas_data,
            "onboarding_completo": True,
            "estado": "activo",
        }).execute()
        vivero_id = vivero_resp.data[0]["vivero_id"]

        client.table("perfiles").upsert({
            "id": user_id,
            "rol": "viverista",
            "whatsapp_numero": whatsapp,
            "vivero_id": vivero_id,
            "nombre_display": data.nombre,
            "onboarding_ok": True,
        }).execute()

        _refresh_app_metadata(client, user_id, {
            "rol": "viverista",
            "vivero_id": vivero_id,
            "whatsapp_numero": whatsapp,
        })

        return {"ok": True, "rol": "viverista", "vivero_id": vivero_id}

    # ────────── COMPRADOR ──────────
    prefs_dict = (
        data.preferencias.model_dump(exclude_none=True)
        if data.preferencias
        else {}
    )
    if not prefs_dict.get("municipios_operacion"):
        prefs_dict["municipios_operacion"] = [data.municipio]

    cliente_resp = client.table("clientes").insert({
        "nombre_representante": data.nombre,
        "nombre_empresa": data.empresa or data.nombre,
        "tipo_cliente": data.tipo_comprador or "otro",
        "ciudad": data.municipio,
        "telefono": whatsapp,
        "whatsapp_numero": whatsapp,
        "nit": data.nit,
        "habeas_data": data.habeas_data,
        "activo": True,
        "preferencias": prefs_dict,
    }).execute()
    cliente_id = cliente_resp.data[0]["cliente_id"]

    client.table("perfiles").upsert({
        "id": user_id,
        "rol": "comprador",
        "whatsapp_numero": whatsapp,
        "cliente_id": cliente_id,
        "nombre_display": data.nombre,
        "onboarding_ok": True,
    }).execute()

    _refresh_app_metadata(client, user_id, {
        "rol": "comprador",
        "cliente_id": cliente_id,
        "whatsapp_numero": whatsapp,
    })

    return {"ok": True, "rol": "comprador", "cliente_id": cliente_id}
