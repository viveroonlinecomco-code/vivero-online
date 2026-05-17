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
5. Al completar onboarding, refrescamos `app_metadata` en Supabase Auth para
   que el rol se propague a futuros tokens (defense-in-depth: complementa el
   fallback DB en /api/auth/me).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, status

from app.schemas.auth import OnboardingRequest
from app.services.supabase import admin
from app.services.twilio_otp import get_otp_service


logger = logging.getLogger(__name__)


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
    profile_resp = client.table("perfiles").select(
        "rol, vivero_id, cliente_id, onboarding_ok"
    ).eq("id", user_id).execute()
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
    """Retorna (user_id, is_new). Busca usuario existente vía SQL directo."""
    # Busca usuario existente por email vía API REST directa (más rápido que list_users)
    try:
        from app.config import get_settings
        import httpx
        s = get_settings()
        headers = {
            "apikey": s.supabase_service_key,
            "Authorization": f"Bearer {s.supabase_service_key}",
        }
        resp = httpx.get(
            f"{s.supabase_url}/auth/v1/admin/users",
            headers=headers,
            params={"email": email},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            users = data.get("users") if isinstance(data, dict) else data
            if users:
                for u in users:
                    if u.get("email") == email:
                        return u.get("id"), False
    except Exception as e:
        logger.warning(f"Error buscando usuario por email: {e}")

    # Si no se encontró, crea uno nuevo
    try:
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
    except Exception as e:
        # Si la creación falla porque el usuario ya existe, intentar buscarlo por SQL
        logger.warning(f"Error en create_user: {e}")
        try:
            from app.services.supabase import admin
            db = admin()
            result = db.from_("auth.users").select("id").eq("email", email).limit(1).execute()
            if result.data:
                return result.data[0]["id"], False
        except Exception:
            pass
        raise HTTPException(500, detail=f"No se pudo crear ni encontrar el usuario: {e}")


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


# ─────────────────── APP_METADATA REFRESH ───────────────────

def _refresh_app_metadata(client, user_id: str, fields: dict) -> None:
    """Mergea `fields` en el app_metadata del usuario en Supabase Auth.

    Esto propaga el rol y los IDs (vivero_id / cliente_id) al JWT en futuros
    refresh de token. NO afecta al token actual del usuario (ese se generó
    pre-onboarding y no tiene rol) — para eso está el fallback DB en /me.

    Si la API de Supabase falla, NO se interrumpe el onboarding: el perfil
    ya quedó creado en la tabla `perfiles` y el endpoint /api/auth/me usa
    esa fuente prioritariamente.
    """
    try:
        # 1. Fetch app_metadata actual para no clobberear campos existentes
        current = client.auth.admin.get_user_by_id(user_id)
        existing = {}
        if current and getattr(current, "user", None):
            existing = current.user.app_metadata or {}

        # 2. Merge: existing keys + new fields (fields ganan en conflictos)
        merged = {**existing, **fields}

        # 3. Update
        client.auth.admin.update_user_by_id(user_id, {"app_metadata": merged})
        logger.info(
            "app_metadata actualizado para %s: rol=%s",
            user_id, fields.get("rol"),
        )
    except Exception as e:
        logger.warning(
            "No se pudo actualizar app_metadata para %s: %s. "
            "Fallback DB en /api/auth/me sigue funcionando.",
            user_id, e,
        )


# ─────────────────── ONBOARDING ───────────────────

async def complete_onboarding(user_id: str, whatsapp: str, data: OnboardingRequest) -> dict:
    """Crea perfil + (viveros | clientes) según el rol elegido.

    Además refresca el `app_metadata` del usuario en Supabase Auth para que
    el rol se propague al JWT en futuros refresh de token (defense-in-depth).

    Phase 1 (mayo 2026): el comprador puede traer un sub-objeto `preferencias`
    (schema soft) que se guarda en `clientes.preferencias` JSONB.
    """
    client = admin()

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

        # Propagar rol al JWT (futuros tokens)
        _refresh_app_metadata(client, user_id, {
            "rol": "viverista",
            "vivero_id": vivero_id,
            "whatsapp_numero": whatsapp,
        })

        return {"ok": True, "rol": "viverista", "vivero_id": vivero_id}

    # ────────── COMPRADOR ──────────
    # OJO: columnas reales son nombre_empresa, ciudad, nombre_representante

    # Phase 1 — serializar schema soft de preferencias → dict para JSONB.
    # supabase-py serializa automáticamente dict Python → jsonb Postgres.
    prefs_dict = (
        data.preferencias.model_dump(exclude_none=True)
        if data.preferencias
        else {}
    )
    # Fallback: si municipios_operacion vino vacío, default al municipio de sede
    # (mejor tener un valor sensato que un array vacío para queries futuras)
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
        "preferencias": prefs_dict,    # NUEVO — Phase 1
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

    # Propagar rol al JWT (futuros tokens)
    _refresh_app_metadata(client, user_id, {
        "rol": "comprador",
        "cliente_id": cliente_id,
        "whatsapp_numero": whatsapp,
    })

    return {"ok": True, "rol": "comprador", "cliente_id": cliente_id}
