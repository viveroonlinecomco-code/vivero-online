"""Rutas de autenticación: OTP por email + onboarding."""
import logging

from fastapi import APIRouter, Depends

from app.auth.deps import require_user, UserContext
from app.auth.flow import send_otp, verify_otp, complete_onboarding
from app.schemas.auth import (
    OtpSendRequest, OtpSendResponse,
    OtpVerifyRequest, OtpVerifyResponse,
    OnboardingRequest, OnboardingResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/otp/send", response_model=OtpSendResponse)
async def api_otp_send(req: OtpSendRequest):
    return await send_otp(req.email)


@router.post("/otp/verify", response_model=OtpVerifyResponse)
async def api_otp_verify(req: OtpVerifyRequest):
    return await verify_otp(req.email, req.code)


@router.post("/onboarding", response_model=OnboardingResponse)
async def api_onboarding(
    req: OnboardingRequest,
    user: UserContext = Depends(require_user),
):
    return await complete_onboarding(user.user_id, req)


@router.get("/me")
async def api_me(user: UserContext = Depends(require_user)):
    """Devuelve el contexto actual del usuario autenticado.

    La tabla `perfiles` es source-of-truth para `rol`, `vivero_id` y `cliente_id`.
    El JWT se usa solo como fallback cuando todavía no existe el perfil en DB.
    """
    from app.services.supabase import admin
    db = admin()
    profile = db.table("perfiles").select(
        "rol, vivero_id, cliente_id, nombre_display, whatsapp_numero"
    ).eq("id", user.user_id).limit(1).execute()
    p = profile.data[0] if profile.data else {}

    db_rol = p.get("rol")
    db_vivero = p.get("vivero_id")
    db_cliente = p.get("cliente_id")
    final_rol = db_rol if db_rol else user.rol
    final_vivero = db_vivero if db_vivero is not None else user.vivero_id
    final_cliente = db_cliente if db_cliente is not None else user.cliente_id

    if user.rol and db_rol and user.rol != db_rol:
        logger.warning(
            "JWT/DB rol discrepancy for user %s: JWT=%s, DB=%s. Using DB.",
            user.user_id, user.rol, db_rol,
        )

    tipo_cliente = None
    if final_cliente:
        try:
            cli = db.table("clientes").select("tipo_cliente").eq(
                "cliente_id", final_cliente
            ).limit(1).execute()
            if cli.data:
                tipo_cliente = cli.data[0].get("tipo_cliente")
        except Exception:
            tipo_cliente = None

    return {
        "user_id": user.user_id,
        "whatsapp": user.whatsapp or p.get("whatsapp_numero"),
        "rol": final_rol,
        "vivero_id": final_vivero,
        "cliente_id": final_cliente,
        "nombre_display": p.get("nombre_display"),
        "tipo_cliente": tipo_cliente,
    }
