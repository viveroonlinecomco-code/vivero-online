"""Flujo de autenticación WhatsApp OTP → Supabase Auth.

Estrategia (porque Supabase Auth no tiene WhatsApp nativo):
1. Twilio Verify valida el OTP.
2. Backend crea o recupera un usuario en auth.users usando el email sintético
   `{numero}@whatsapp.vivero.online` (es una convención interna, no se usa nunca
    para email real - Supabase exige email único para cada usuario).
3. Backend genera una sesión (access + refresh token) con admin.generate_link
   o admin.create_user + impersonate.
4. El JWT resultante ya contiene `whatsapp_numero` en sus claims (vía el
   hook `custom_access_token_hook` que ya está instalado en la BD - migración 14).
"""
from __future__ import annotations

from fastapi import HTTPException, status

from app.schemas.auth import OnboardingRequest
from app.services.supabase import admin
from app.services.twilio_otp import get_otp_service


def _synthetic_email(whatsapp: str) -> str:
    """+573001234567 -> 573001234567@whatsapp.vivero.online"""
    return f"{whatsapp.lstrip('+')}@whatsapp.vivero.online"


# ─────────────────── SEND OTP ───────────────────

async def send_otp(whatsapp: str) -> dict:
    """Envía OTP por WhatsApp via Twilio Verify."""
    otp = get_otp_service()
    ok, status_msg = otp.send(whatsapp)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo enviar el código: {status_msg}",
        )
    return {
        "ok": True,
        "message": "Código enviado a tu WhatsApp",
        "delivered_via": "whatsapp",
    }


# ─────────────────── VERIFY OTP ───────────────────

async def verify_otp(whatsapp: str, code: str) -> dict:
    """Valida OTP, crea o recupera usuario Supabase, retorna sesión."""
    # 1. Twilio valida
    otp = get_otp_service()
    ok, status_msg = otp.check(whatsapp, code)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Código incorrecto o expirado",
        )

    # 2. Busca / crea usuario en Supabase Auth
    client = admin()
    email = _synthetic_email(whatsapp)
    user_id, is_new = _get_or_create_user(client, email, whatsapp)

    # 3. Genera sesión (magic link sign-in sin email real)
    tokens = _generate_session(client, email)

    # 4. Mira si ya tiene perfil completo
    profile_resp = client.table("perfiles").select("rol, vivero_id, cliente_id, onboarding_ok").eq("id", user_id).execute()
    rows = profile_resp.data or []

    needs_onboarding = True
    rol = None
    if rows:
        p = rows[0]
        if p.get("onboarding_ok"):
            needs_onboarding = False
            rol = p.get("rol")

    return {
        "ok": True,
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "user_id": user_id,
        "whatsapp": whatsapp,
        "needs_onboarding": needs_onboarding,
        "rol": rol,
    }


def _get_or_create_user(client, email: str, whatsapp: str) -> tuple[str, bool]:
       """Retorna (user_id, is_new)."""
       # Busca por email - paginado para soportar > 50 usuarios
       try:
           page = 1
           while page < 50:  # max 50 páginas = 2500 usuarios
               result = client.auth.admin.list_users(page=page, per_page=50)
               users_list = result if isinstance(result, list) else (result.users if hasattr(result, 'users') else [])
               if not users_list:
                   break
               for u in users_list:
                   if u.email == email or (hasattr(u, 'phone') and u.phone and whatsapp.lstrip('+') in u.phone):
                       return u.id, False
               if len(users_list) < 50:
                   break
               page += 1
       except Exception as e:
           import logging
           logging.warning(f"Error buscando usuario: {e}")

    # Crea
    created = client.auth.admin.create_user({
        "email": email,
        "email_confirm": True,
        "phone": whatsapp,
        "user_metadata": {"whatsapp_numero": whatsapp},
        "app_metadata": {"whatsapp_numero": whatsapp, "provider": "whatsapp_otp"},
    })
    if not created or not created.user:
        raise HTTPException(500, detail="No se pudo crear el usuario")
    return created.user.id, True


def _generate_session(client, email: str) -> dict:
    """Genera access_token + refresh_token sin requerir password/email verify.

    Usa admin.generate_link con type='magiclink' y consume el token
    con verifyOtp para obtener la sesión. Es una técnica estándar
    para auth personalizada en Supabase.
    """
    link_resp = client.auth.admin.generate_link({
        "type": "magiclink",
        "email": email,
    })
    # El `hashed_token` del response puede ser consumido con verify_otp
    props = getattr(link_resp, "properties", None) or {}
    hashed = props.get("hashed_token") if isinstance(props, dict) else getattr(props, "hashed_token", None)
    if not hashed:
        # Algunas versiones retornan en .action_link; extraemos query param
        action_link = props.get("action_link") if isinstance(props, dict) else getattr(props, "action_link", None)
        if action_link and "token=" in action_link:
            hashed = action_link.split("token=")[1].split("&")[0]

    if not hashed:
        raise HTTPException(500, detail="No se pudo generar sesión")

    session_resp = client.auth.verify_otp({
        "token_hash": hashed,
        "type": "magiclink",
    })
    sess = session_resp.session
    if not sess:
        raise HTTPException(500, detail="Sesión nula")

    return {
        "access_token": sess.access_token,
        "refresh_token": sess.refresh_token,
    }


# ─────────────────── ONBOARDING ───────────────────

async def complete_onboarding(user_id: str, whatsapp: str, data: OnboardingRequest) -> dict:
    """Crea perfil + (viveros | clientes) según el rol elegido."""
    client = admin()

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

        return {"ok": True, "rol": "viverista", "vivero_id": vivero_id}

    # Comprador - OJO: columnas reales son nombre_empresa, ciudad, nombre_representante
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

    return {"ok": True, "rol": "comprador", "cliente_id": cliente_id}
