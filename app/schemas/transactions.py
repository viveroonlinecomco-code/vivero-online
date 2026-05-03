"""Schemas para cotizaciones, transacciones, pagos y chat."""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class CotizacionItem(BaseModel):
    inventario_id: int
    cantidad: int = Field(..., ge=1)


class CotizacionRequest(BaseModel):
    items: List[CotizacionItem] = Field(..., min_length=1)
    notas: Optional[str] = None
    proyecto: Optional[str] = None  # Nombre del proyecto del comprador


class CotizacionResponse(BaseModel):
    ok: bool
    cotizacion_id: int
    total_cop: float
    estado: str


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
