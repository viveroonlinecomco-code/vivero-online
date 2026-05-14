"""Rutas del catálogo del viverista.

- GET    /api/catalogo                       → lista inventario propio
- POST   /api/catalogo/identificar           → sube foto, IA la identifica
- POST   /api/catalogo/guardar               → confirma y guarda en inventario
- PATCH  /api/catalogo/inventario/{id}       → actualiza stock / precio / estado
- DELETE /api/catalogo/inventario/{id}       → elimina item del inventario
"""
from __future__ import annotations
import io
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from PIL import Image

from app.auth.deps import UserContext, require_viverista
from app.agents.plant_identifier import PlantIdentifierAgent
from app.schemas.catalog import (
    CatalogoResponse,
    GuardarInventarioRequest,
    IdentificarResponse,
    InventarioItem,
)
from app.services.supabase import admin


router = APIRouter(prefix="/api/catalogo", tags=["catalogo"])


# ─────────────────── LISTAR CATÁLOGO ───────────────────

@router.get("", response_model=CatalogoResponse)
async def listar_catalogo(user: UserContext = Depends(require_viverista)):
    """Lista el inventario del viverista actual."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    db = admin()
    resp = db.table("inventario").select(
        "inventario_id, planta_id, altura_cm, precio_mayorista, precio_detal, "
        "stock, unidad_medida, estado_planta, foto_ia_url, "
        "plantas(nombre_comun, nombre_cientifico)"
    ).eq("vivero_id", user.vivero_id).order("fecha_actualizacion", desc=True).execute()

    items: list[InventarioItem] = []
    for r in resp.data or []:
        planta = r.get("plantas") or {}
        items.append(InventarioItem(
            inventario_id=r["inventario_id"],
            planta_id=r["planta_id"],
            nombre_comun=planta.get("nombre_comun", "Sin nombre"),
            nombre_cientifico=planta.get("nombre_cientifico"),
            foto_ia_url=r.get("foto_ia_url"),
            precio_mayorista=float(r.get("precio_mayorista") or 0),
            precio_detal=float(r["precio_detal"]) if r.get("precio_detal") else None,
            stock=r.get("stock") or 0,
            altura_cm=r.get("altura_cm") or 0,
            unidad_medida=r.get("unidad_medida") or "unidad",
            estado_planta=r.get("estado_planta") or "disponible",
        ))

    return CatalogoResponse(ok=True, items=items, total=len(items))


# ─────────────────── IDENTIFICAR CON IA ───────────────────

@router.post("/identificar", response_model=IdentificarResponse)
async def identificar_planta(
    imagen: UploadFile = File(...),
    user: UserContext = Depends(require_viverista),
):
    """Sube foto → Gemini Vision identifica → sube a bucket → retorna análisis.
    NO guarda en inventario aún (eso lo hace /guardar)."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    # Validar tipo
    if not imagen.content_type or not imagen.content_type.startswith("image/"):
        raise HTTPException(400, detail="Debe ser una imagen")

    raw = await imagen.read()
    if len(raw) == 0:
        raise HTTPException(400, detail="Imagen vacía")
    if len(raw) > 10 * 1024 * 1024:  # 10MB
        raise HTTPException(413, detail="Imagen muy grande (máx 10MB)")

    # Normalizar a JPEG para YOLO/Gemini + ahorrar en storage
    try:
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # Redimensionar si es muy grande
        max_side = 1600
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        image_bytes = buf.getvalue()
    except Exception as e:
        raise HTTPException(400, detail=f"Imagen inválida: {e}")

    # YOLO preprocessing: detectar y recortar la planta del fondo (si está habilitado)
    from app.services.yolo import get_yolo
    yolo = get_yolo()
    cropped_bytes, yolo_meta = await yolo.crop_plant(image_bytes)

    # Identificar con IA (Gemini sobre la imagen recortada si hubo crop)
    agent = PlantIdentifierAgent()
    analisis = agent.identify_from_bytes(cropped_bytes, "image/jpeg")

    # Si YOLO recortó, reflejarlo en confianza_yolo del análisis
    if yolo_meta.get("yolo_used"):
        analisis.confianza = max(
            analisis.confianza,
            float(yolo_meta.get("confidence") or 0) * 0.5 + analisis.confianza * 0.5,
        )

    # Subir al bucket (path: vivero_id/uuid.jpg)
    db = admin()
    filename = f"{user.vivero_id}/{uuid.uuid4()}.jpg"
    try:
        db.storage.from_("plantas-fotos").upload(
            path=filename,
            file=image_bytes,
            file_options={"content-type": "image/jpeg"},
        )
        public_url = db.storage.from_("plantas-fotos").get_public_url(filename)
    except Exception as e:
        raise HTTPException(500, detail=f"Error al subir imagen: {e}")

    # Buscar si ya existe la planta en el catálogo base (por nombre científico)
    planta_id: Optional[int] = None
    if analisis.nombre_cientifico:
        existing = db.table("plantas").select("planta_id").eq(
            "nombre_cientifico", analisis.nombre_cientifico
        ).limit(1).execute()
        if existing.data:
            planta_id = existing.data[0]["planta_id"]

    return IdentificarResponse(
        ok=True,
        analisis=analisis,
        foto_url=public_url,
        planta_id=planta_id,
        yolo_meta=yolo_meta,
    )


# ─────────────────── GUARDAR EN INVENTARIO ───────────────────

@router.post("/guardar")
async def guardar_inventario(
    req: GuardarInventarioRequest,
    user: UserContext = Depends(require_viverista),
):
    """Crea planta si no existe + crea item de inventario."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    db = admin()

    # 1. Obtener o crear planta en el catálogo base
    planta_id = req.planta_id
    if not planta_id:
        planta_resp = db.table("plantas").insert({
            "nombre_comun": req.nombre_comun,
            "nombre_cientifico": req.nombre_cientifico,
            "activa": True,
        }).execute()
        planta_id = planta_resp.data[0]["planta_id"]

    # 2. Crear item de inventario
    inv_resp = db.table("inventario").insert({
        "vivero_id": user.vivero_id,
        "planta_id": planta_id,
        "altura_cm": req.altura_cm,
        "precio_mayorista": req.precio_mayorista,
        "precio_detal": req.precio_detal,
        "stock": req.stock,
        "unidad_medida": req.unidad_medida,
        "foto_ia_url": req.foto_url,
        "confianza_yolo": req.confianza_yolo,
        "estado_planta": "disponible",
        "origen_carga": "ia_viverista",
        "notas": req.notas,
    }).execute()

    return {
        "ok": True,
        "inventario_id": inv_resp.data[0]["inventario_id"],
        "planta_id": planta_id,
    }


# ─────────────────── ACTUALIZAR INVENTARIO ───────────────────

ESTADOS_INVENTARIO = ("disponible", "agotado", "reservado", "en_crecimiento")


class ActualizarInventarioRequest(BaseModel):
    """Campos editables de un item del inventario (todos opcionales)."""
    stock: Optional[int] = Field(default=None, ge=0)
    precio_mayorista: Optional[float] = Field(default=None, ge=0)
    precio_detal: Optional[float] = Field(default=None, ge=0)
    estado_planta: Optional[str] = Field(default=None)
    notas: Optional[str] = Field(default=None, max_length=1000)


@router.patch("/inventario/{inventario_id}")
async def actualizar_inventario(
    inventario_id: int,
    req: ActualizarInventarioRequest,
    user: UserContext = Depends(require_viverista),
):
    """Actualiza campos de un item del inventario.

    Solo el viverista dueño puede modificarlo (chequeo explícito de ownership
    + RLS de Postgres como defense-in-depth).
    """
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    # Validar estado_planta si fue enviado
    if req.estado_planta is not None and req.estado_planta not in ESTADOS_INVENTARIO:
        raise HTTPException(
            400,
            detail=f"Estado inválido. Válidos: {', '.join(ESTADOS_INVENTARIO)}",
        )

    db = admin()

    # Verificar que el item pertenece al vivero del usuario
    existing = db.table("inventario").select("vivero_id").eq(
        "inventario_id", inventario_id
    ).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, detail="Item no encontrado")
    if existing.data[0]["vivero_id"] != user.vivero_id:
        raise HTTPException(403, detail="Este item no pertenece a tu vivero")

    # Construir payload solo con campos no-None (evita pisar valores con null)
    payload: dict = {}
    if req.stock is not None:
        payload["stock"] = req.stock
    if req.precio_mayorista is not None:
        payload["precio_mayorista"] = req.precio_mayorista
    if req.precio_detal is not None:
        payload["precio_detal"] = req.precio_detal
    if req.estado_planta is not None:
        payload["estado_planta"] = req.estado_planta
    if req.notas is not None:
        payload["notas"] = req.notas.strip() or None

    if not payload:
        raise HTTPException(400, detail="No hay campos para actualizar")

    resp = db.table("inventario").update(payload).eq(
        "inventario_id", inventario_id
    ).execute()

    if not resp.data:
        raise HTTPException(500, detail="No se pudo actualizar el inventario")

    return {
        "ok": True,
        "inventario_id": inventario_id,
        "actualizado": payload,
    }


# ─────────────────── ELIMINAR INVENTARIO ───────────────────

@router.delete("/inventario/{inventario_id}")
async def eliminar_inventario(
    inventario_id: int,
    user: UserContext = Depends(require_viverista),
):
    """Elimina un item del inventario.

    Si el item tiene cotizaciones o transacciones asociadas, el DELETE
    falla por FK constraint. En ese caso se sugiere cambiar el estado a
    'agotado' en lugar de borrar (preserva integridad histórica).
    """
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    db = admin()

    # Verificar ownership
    existing = db.table("inventario").select("vivero_id").eq(
        "inventario_id", inventario_id
    ).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, detail="Item no encontrado")
    if existing.data[0]["vivero_id"] != user.vivero_id:
        raise HTTPException(403, detail="Este item no pertenece a tu vivero")

    try:
        db.table("inventario").delete().eq("inventario_id", inventario_id).execute()
    except Exception as e:
        msg = str(e).lower()
        if "foreign" in msg or "violates" in msg or "referenced" in msg:
            raise HTTPException(
                409,
                detail=(
                    "No se puede eliminar: este item tiene cotizaciones o "
                    "transacciones asociadas. Cambiá el estado a 'agotado' "
                    "en su lugar para mantenerlo fuera del marketplace."
                ),
            )
        raise HTTPException(500, detail=f"Error al eliminar: {str(e)[:200]}")

    return {"ok": True, "inventario_id": inventario_id, "eliminado": True}
