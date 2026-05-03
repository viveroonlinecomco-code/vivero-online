"""Schemas Pydantic para auth: OTP + onboarding."""
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator
import re


WHATSAPP_REGEX = re.compile(r"^\+57\d{10}$")  # Colombia: +57 + 10 dígitos


class OtpSendRequest(BaseModel):
    """Solicitud para enviar código OTP por WhatsApp."""
    whatsapp: str = Field(..., description="Número E.164, ej: +573001234567")

    @field_validator("whatsapp")
    @classmethod
    def validate_whatsapp(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if not WHATSAPP_REGEX.match(v):
            raise ValueError("El número debe ser colombiano en formato +57XXXXXXXXXX")
        return v


class OtpSendResponse(BaseModel):
    """Respuesta al envío de OTP."""
    ok: bool
    message: str
    delivered_via: Literal["whatsapp", "sms"] = "whatsapp"


class OtpVerifyRequest(BaseModel):
    """Verifica el código OTP ingresado."""
    whatsapp: str
    code: str = Field(..., min_length=4, max_length=10)

    @field_validator("whatsapp")
    @classmethod
    def validate_whatsapp(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if not WHATSAPP_REGEX.match(v):
            raise ValueError("Número inválido")
        return v

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit():
            raise ValueError("El código debe ser numérico")
        return v


class OtpVerifyResponse(BaseModel):
    """Respuesta con sesión Supabase + estado del perfil."""
    ok: bool
    access_token: str
    refresh_token: str
    user_id: str
    whatsapp: str
    needs_onboarding: bool
    rol: Optional[Literal["admin", "viverista", "comprador"]] = None


class OnboardingRequest(BaseModel):
    """Datos del primer registro después del OTP."""
    rol: Literal["viverista", "comprador"]
    nombre: str = Field(..., min_length=2, max_length=120)
    municipio: str = Field(..., min_length=2, max_length=80)
    nit: Optional[str] = None
    habeas_data: bool

    # Viverista
    nombre_vivero: Optional[str] = None

    # Comprador
    empresa: Optional[str] = None
    tipo_comprador: Optional[Literal[
        "paisajista", "constructora", "conjunto", "empresa", "otro"
    ]] = None

    @field_validator("habeas_data")
    @classmethod
    def must_accept_habeas(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Debes aceptar el tratamiento de datos")
        return v


class OnboardingResponse(BaseModel):
    ok: bool
    rol: str
    vivero_id: Optional[int] = None
    cliente_id: Optional[int] = None
