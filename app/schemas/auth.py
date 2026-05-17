"""Schemas Pydantic para autenticación: OTP + onboarding."""
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, field_validator


# ─────────────────── CONSTANTES DE VALIDACIÓN ───────────────────

TIPOS_PROYECTOS_VALIDOS = {
    "residencial", "comercial", "urbano", "institucional", "otro",
}
TIPOS_COMPRADOR_VALIDOS = {
    "paisajista", "constructora", "conjunto", "empresa", "otro",
}


# ─────────────────── OTP SEND / VERIFY ───────────────────

class OtpSendRequest(BaseModel):
    """Request para enviar OTP por WhatsApp."""
    whatsapp: str = Field(..., pattern=r"^\+?\d{10,15}$",
                          description="Número internacional, con o sin '+'")


class OtpSendResponse(BaseModel):
    """Confirmación de envío de OTP."""
    ok: bool
    message: str
    delivered_via: str


class OtpVerifyRequest(BaseModel):
    """Request para validar el código OTP recibido."""
    whatsapp: str = Field(..., pattern=r"^\+?\d{10,15}$")
    code: str = Field(..., min_length=4, max_length=10,
                      description="Código de 6 dígitos enviado por Twilio")


class OtpVerifyResponse(BaseModel):
    """Sesión emitida tras validar OTP correctamente."""
    ok: bool
    access_token: str
    refresh_token: str
    user_id: str
    whatsapp: str
    needs_onboarding: bool
    rol: Optional[str] = None


# ─────────────────── ONBOARDING ───────────────────

class CompradorPreferencias(BaseModel):
    """Perfil enriquecido del comprador — schema soft (se persiste como JSONB).

    Diseño deliberado: en vez de columnas estructuradas en `clientes`,
    todo va anidado en `clientes.preferencias` JSONB. Esto permite al
    equipo comercial agregar nuevas preguntas (interes_nativas,
    volumen_mensual_estimado, frecuencia_compra, estilo_paisajismo, etc.)
    sin migraciones SQL — solo se descomenta acá y se agrega el control
    al template.
    """
    descripcion: Optional[str] = Field(None, max_length=280,
                                       description="Bio corta del comprador")
    web: Optional[str] = Field(None, max_length=200,
                               description="URL de página web (opcional)")
    instagram: Optional[str] = Field(None, max_length=80,
                                     description="Handle de IG, con o sin @")
    tipos_proyectos: List[str] = Field(
        default_factory=list,
        description="Tipos de proyectos que maneja: residencial, comercial, "
                    "urbano, institucional, otro",
    )
    municipios_operacion: List[str] = Field(
        default_factory=list,
        description="Municipios donde opera el comprador (no solo su sede)",
    )

    # ── Campos futuros que el equipo comercial puede activar sin tocar DB.
    #    Solo descomentar acá + sumar control al form en auth_onboarding.html ──
    # interes_nativas: Optional[bool] = None
    # volumen_mensual_estimado: Optional[Literal["bajo", "medio", "alto"]] = None
    # frecuencia_compra: Optional[Literal["puntual", "mensual", "trimestral"]] = None
    # estilo_paisajismo: Optional[str] = None

    @field_validator("tipos_proyectos")
    @classmethod
    def tipos_proyectos_validos(cls, v):
        invalidos = [t for t in v if t not in TIPOS_PROYECTOS_VALIDOS]
        if invalidos:
            raise ValueError(
                f"tipos_proyectos inválidos: {invalidos}. "
                f"Válidos: {sorted(TIPOS_PROYECTOS_VALIDOS)}"
            )
        return v


class OnboardingRequest(BaseModel):
    """Datos para completar el onboarding tras OTP verificado.

    Convención de nombres de campo: mantiene los que ya usabas en `flow.py`
    (`nombre`, `empresa`, `tipo_comprador`) — no se renombran para no
    romper el código existente.
    """
    rol: Literal["viverista", "comprador"]
    nombre: str = Field(..., min_length=2, max_length=120,
                        description="Nombre completo del usuario")
    municipio: str = Field(..., min_length=1,
                           description="Municipio de sede del usuario")
    nit: Optional[str] = Field(None, max_length=30,
                               description="NIT o cédula, opcional")
    habeas_data: bool = Field(..., description="Aceptación Ley 1581 — obligatorio True")

    # ─── Viverista ───
    nombre_vivero: Optional[str] = Field(None, max_length=120,
                                         description="Nombre comercial del vivero")

    # ─── Comprador básicos ───
    empresa: Optional[str] = Field(None, max_length=160,
                                   description="Empresa u organización del comprador")
    tipo_comprador: Optional[str] = Field(
        None,
        description="paisajista | constructora | conjunto | empresa | otro",
    )

    # ─── Comprador perfil enriquecido (JSONB en DB) ───
    preferencias: Optional[CompradorPreferencias] = None

    @field_validator("tipo_comprador")
    @classmethod
    def tipo_comprador_valido(cls, v):
        if v is not None and v not in TIPOS_COMPRADOR_VALIDOS:
            raise ValueError(
                f"tipo_comprador inválido. "
                f"Válidos: {sorted(TIPOS_COMPRADOR_VALIDOS)}"
            )
        return v

    @field_validator("habeas_data")
    @classmethod
    def habeas_obligatorio(cls, v):
        if v is not True:
            raise ValueError("habeas_data debe ser True para completar el registro")
        return v


class OnboardingResponse(BaseModel):
    """Confirmación de onboarding completado.

    Devuelve `vivero_id` para viveristas y `cliente_id` para compradores —
    el otro queda en None.
    """
    ok: bool
    rol: str
    vivero_id: Optional[int] = None
    cliente_id: Optional[int] = None
