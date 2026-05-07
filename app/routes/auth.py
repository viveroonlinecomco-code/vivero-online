"""Rutas de autenticación: OTP + onboarding."""
from fastapi import APIRouter, Depends, HTTPException

from app.auth.deps import require_user, UserContext
from app.auth.flow import send_otp, verify_otp, complete_onboarding
from app.schemas.auth import (
    OtpSendRequest, OtpSendResponse,
    OtpVerifyRequest, OtpVerifyResponse,
    OnboardingRequest, OnboardingResponse,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/otp/send", response_model=OtpSendResponse)
async def api_otp_send(req: OtpSendRequest):
    return await send_otp(req.whatsapp)


@router.post("/otp/verify", response_model=OtpVerifyResponse)
async def api_otp_verify(req: OtpVerifyRequest):
    return await verify_otp(req.whatsapp, req.code)


@router.post("/onboarding", response_model=OnboardingResponse)
async def api_onboarding(
    req: OnboardingRequest,
    user: UserContext = Depends(require_user),
):
    if not user.whatsapp:
        raise HTTPException(400, detail="WhatsApp no presente en el token")
    return await complete_onboarding(user.user_id, user.whatsapp, req)


@router.get("/me")
async def api_me(user: UserContext = Depends(require_user)):
    """Devuelve el contexto actual del usuario autenticado.

    Si el usuario es comprador, también devuelve `tipo_cliente`
    (paisajista | constructora | conjunto | empresa | otro) para que el
    frontend pueda personalizar el saludo. Es solo metadata, NO un rol.
    """
    from app.services.supabase import admin
    db = admin()
    profile = db.table("perfiles").select(
        "rol, vivero_id, cliente_id, nombre_display, whatsapp_numero"
    ).eq("id", user.user_id).limit(1).execute()
    p = profile.data[0] if profile.data else {}

    cliente_id = user.cliente_id or p.get("cliente_id")
    tipo_cliente = None
    if cliente_id:
        try:
            cli = db.table("clientes").select("tipo_cliente").eq(
                "cliente_id", cliente_id
            ).limit(1).execute()
            if cli.data:
                tipo_cliente = cli.data[0].get("tipo_cliente")
        except Exception:
            tipo_cliente = None

    return {
        "user_id": user.user_id,
        "whatsapp": user.whatsapp or p.get("whatsapp_numero"),
        "rol": user.rol or p.get("rol"),
        "vivero_id": user.vivero_id or p.get("vivero_id"),
        "cliente_id": cliente_id,
        "nombre_display": p.get("nombre_display"),
        "tipo_cliente": tipo_cliente,  # solo presente si rol=comprador
    }
