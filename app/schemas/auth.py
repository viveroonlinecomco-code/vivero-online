from typing import Optional, List, Literal
from pydantic import BaseModel, Field, field_validator


TIPOS_PROYECTOS_VALIDOS = {"residencial", "comercial", "urbano", "institucional", "otro"}
TIPOS_CLIENTE_VALIDOS = {"paisajista", "constructora", "conjunto", "empresa", "otro"}


class CompradorPreferencias(BaseModel):
    """Perfil enriquecido del comprador — schema soft.

    Agregar un campo nuevo NO requiere migración SQL: solo se suma acá
    y se maneja en frontend + flow. La tabla `clientes.preferencias`
    lo absorbe en su JSONB.
    """
    descripcion: Optional[str] = Field(None, max_length=280)
    web: Optional[str] = Field(None, max_length=200)
    instagram: Optional[str] = Field(None, max_length=80)
    tipos_proyectos: List[str] = Field(default_factory=list)
    municipios_operacion: List[str] = Field(default_factory=list)

    # ── Campos futuros que el equipo comercial puede activar cuando quiera,
    #    sin tocar DB. Solo descomentar acá y agregar el control al form: ──
    # interes_nativas: Optional[bool] = None
    # volumen_mensual_estimado: Optional[Literal["bajo", "medio", "alto"]] = None
    # frecuencia_compra: Optional[Literal["puntual", "mensual", "trimestral"]] = None
    # estilo_paisajismo: Optional[str] = None

    @field_validator("tipos_proyectos")
    @classmethod
    def tipos_proyectos_validos(cls, v):
        invalidos = [t for t in v if t not in TIPOS_PROYECTOS_VALIDOS]
        if invalidos:
            raise ValueError(f"tipos_proyectos inválidos: {invalidos}")
        return v


class OnboardingRequest(BaseModel):
    rol: Literal["viverista", "comprador"]
    nombre_display: str = Field(..., min_length=2, max_length=120)
    municipio: str = Field(..., min_length=1)
    nit: Optional[str] = Field(None, max_length=30)
    habeas_data: bool

    # Viverista
    nombre_vivero: Optional[str] = Field(None, max_length=120)

    # Comprador básicos
    nombre_empresa: Optional[str] = Field(None, max_length=160)
    tipo_cliente: Optional[str] = None

    # Comprador soft profile → JSONB
    preferencias: Optional[CompradorPreferencias] = None

    @field_validator("tipo_cliente")
    @classmethod
    def tipo_cliente_valido(cls, v):
        if v is not None and v not in TIPOS_CLIENTE_VALIDOS:
            raise ValueError(f"tipo_cliente debe ser uno de {sorted(TIPOS_CLIENTE_VALIDOS)}")
        return v
