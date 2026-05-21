"""Schemas para cotizaciones, transacciones, pagos y chat."""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class CotizacionItem(BaseModel):
    inventario_id: int
    cantidad: int = Field(..., ge=1)


class CotizacionRequest(BaseModel):
    """Request para agregar items a una cotización.

    Tres modos de operación (mutuamente excluyentes para los dos primeros):
    1. EXPLÍCITO con `cotizacion_id` → agrega items a un borrador existente.
    2. EXPLÍCITO con `nombre_proyecto` → crea un borrador nuevo con ese nombre.
    3. LEGACY (sin ninguno) → find-or-create: agrega al borrador más reciente
       del comprador (o crea uno sin nombre si no tiene). Compat con el
       frontend del marketplace_detalle.html previo al modal.
    """
    items: List[CotizacionItem] = Field(..., min_length=1)
    notas: Optional[str] = None

    # Nuevos campos para multi-proyecto:
    cotizacion_id: Optional[int] = None
    nombre_proyecto: Optional[str] = Field(None, max_length=200)

    # Legacy (deprecado pero soportado por compat):
    proyecto: Optional[str] = None  # se guarda en prompt_original cuando se usa modo legacy


class CotizacionResponse(BaseModel):
    ok: bool
    cotizacion_id: int
    total_cop: float
    estado: str


class RenameProyectoRequest(BaseModel):
    """Request para renombrar un proyecto (PATCH /cotizacion/{id})."""
    nombre_proyecto: str = Field(..., min_length=1, max_length=200)


class TransaccionOut(BaseModel):
    transaccion_id: int
    cantidad: int
    precio_unitario: float
    precio_total: float
    estado: str
    fecha_transaccion: str
    nombre_planta: Optional[str] = None
    nombre_vivero: Optional[str] = None
    comprador: Optional[str] = None


class TransaccionesResponse(BaseModel):
    ok: bool
    items: List[TransaccionOut]


class ChatRequest(BaseModel):
    mensaje: str = Field(..., min_length=1, max_length=2000)
    historial: List[str] = Field(default_factory=list)
    contexto: Optional[dict] = None


class ChatResponse(BaseModel):
    ok: bool
    respuesta: str
    agente: str  # cuál de los 8 agentes respondió
    metadata: dict = Field(default_factory=dict)


class KpisResponse(BaseModel):
    gmv_total_cop: float
    comision_plataforma_cop: float
    transacciones_validas: int
    ticket_promedio_cop: float
    compradores_activos: int
    viveristas_activos: int
    items_disponibles: int
    viveros_registrados: int
    crecimiento_semanal: List[dict]
    top_viveristas: List[dict]
