"""Schemas para catálogo, inventario e identificación IA."""
from typing import List, Optional
from pydantic import BaseModel, Field


class PlantaIdentificada(BaseModel):
    """Resultado del identificador de IA (Gemini Vision + YOLO)."""
    nombre_comun: str
    nombre_cientifico: Optional[str] = None
    familia_botanica: Optional[str] = None
    descripcion: Optional[str] = None
    cuidados: Optional[str] = None
    luz: Optional[str] = None           # "directa", "indirecta", "sombra"
    riego: Optional[str] = None         # "diario", "semanal", "quincenal"
    advertencias: Optional[str] = None  # toxicidad, plagas, etc
    confianza: float = Field(..., ge=0.0, le=1.0)
    precio_estimado_cop: Optional[int] = None
    altura_cm_estimada: Optional[int] = None


class IdentificarResponse(BaseModel):
    ok: bool
    analisis: PlantaIdentificada
    foto_url: str                       # URL ya subida al bucket plantas-fotos
    planta_id: Optional[int] = None     # ID si ya existe en catálogo base
    yolo_meta: Optional[dict] = None    # Info del preprocessing YOLO (debug)


class GuardarInventarioRequest(BaseModel):
    """Confirma y guarda la planta en el inventario del viverista."""
    planta_id: Optional[int] = None     # Si es None, se crea en plantas
    nombre_comun: str
    nombre_cientifico: Optional[str] = None
    foto_url: str
    precio_mayorista: int = Field(..., ge=0)
    precio_detal: Optional[int] = None
    stock: int = Field(..., ge=1)
    altura_cm: int = Field(..., ge=1)
    unidad_medida: str = "unidad"
    notas: Optional[str] = None
    confianza_yolo: Optional[float] = None


class InventarioItem(BaseModel):
    """Item de inventario para listado."""
    inventario_id: int
    planta_id: int
    nombre_comun: str
    nombre_cientifico: Optional[str] = None
    foto_ia_url: Optional[str] = None
    precio_mayorista: float
    precio_detal: Optional[float] = None
    stock: int
    altura_cm: int
    unidad_medida: str
    estado_planta: str
    # Para marketplace (comprador ve)
    vivero_id: Optional[int] = None
    nombre_vivero: Optional[str] = None
    municipio: Optional[str] = None
    distancia_km: Optional[float] = None


class CatalogoResponse(BaseModel):
    ok: bool
    items: List[InventarioItem]
    total: int


class MarketplaceQuery(BaseModel):
    """Parámetros de búsqueda en el marketplace."""
    q: Optional[str] = None                    # búsqueda libre
    municipio: Optional[str] = None
    radio_km: float = 50.0
    altura_min_cm: int = 0
    cantidad_min: int = 1
    lat: float = 4.9195                        # default: Cajicá
    lon: float = -74.0270
    limite: int = Field(default=20, le=100)
