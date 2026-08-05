"""Rutas del catálogo del viverista.

- GET    /api/catalogo                       → lista inventario propio
- POST   /api/catalogo/identificar           → sube foto, IA la identifica
- POST   /api/catalogo/guardar               → confirma y guarda en inventario
- PATCH  /api/catalogo/inventario/{id}       → actualiza stock / precio / estado / nombre
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

@router.get("")
async def listar_catalogo(user: UserContext = Depends(require_viverista)):
    """Lista el inventario del viverista actual.

    FIX 5 ago 2026: incluir logistics_tier y tier_manual en la respuesta.
    Antes: el frontend recibía siempre 'M' como default porque el SELECT
    no incluía esos campos. Ahora se devuelve dict flexible para no requerir
    cambios en schemas/catalog.py.
    """
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    db = admin()
    resp = db.table("inventario").select(
        "inventario_id, planta_id, altura_cm, precio_mayorista, precio_detal, "
        "stock, unidad_medida, estado_planta, foto_ia_url, "
        "logistics_tier, tier_manual, "
        "plantas(nombre_comun, nombre_cientifico)"
    ).eq("vivero_id", user.vivero_id).order("fecha_actualizacion", desc=True).execute()

    items: list[dict] = []
    for r in resp.data or []:
        planta = r.get("plantas") or {}
        items.append({
            "inventario_id":     r["inventario_id"],
            "planta_id":         r["planta_id"],
            "nombre_comun":      planta.get("nombre_comun", "Sin nombre"),
            "nombre_cientifico": planta.get("nombre_cientifico"),
            "foto_ia_url":       r.get("foto_ia_url"),
            "precio_mayorista":  float(r.get("precio_mayorista") or 0),
            "precio_detal":      float(r["precio_detal"]) if r.get("precio_detal") else None,
            "stock":             r.get("stock") or 0,
            "altura_cm":         r.get("altura_cm") or 0,
            "unidad_medida":     r.get("unidad_medida") or "unidad",
            "estado_planta":     r.get("estado_planta") or "disponible",
            # ── Fase editor de tier (5 ago 2026) ──
            "logistics_tier":    r.get("logistics_tier"),  # tier efectivo (auto o manual)
            "tier_manual":       r.get("tier_manual"),      # override manual (si existe)
        })

    return {"ok": True, "items": items, "total": len(items)}


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

    if not imagen.content_type or not imagen.content_type.startswith("image/"):
        raise HTTPException(400, detail="Debe ser una imagen")

    raw = await imagen.read()
    if len(raw) == 0:
        raise HTTPException(400, detail="Imagen vacía")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, detail="Imagen muy grande (máx 10MB)")

    try:
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        max_side = 1600
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        image_bytes = buf.getvalue()
    except Exception as e:
        raise HTTPException(400, detail=f"Imagen inválida: {e}")

    from app.services.yolo import get_yolo
    yolo = get_yolo()
    cropped_bytes, yolo_meta = await yolo.crop_plant(image_bytes)

    agent = PlantIdentifierAgent()
    analisis = agent.identify_from_bytes(cropped_bytes, "image/jpeg")

    if yolo_meta.get("yolo_used"):
        analisis.confianza = max(
            analisis.confianza,
            float(yolo_meta.get("confidence") or 0) * 0.5 + analisis.confianza * 0.5,
        )

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

    planta_id = req.planta_id
    if not planta_id:
        planta_resp = db.table("plantas").insert({
            "nombre_comun": req.nombre_comun,
            "nombre_cientifico": req.nombre_cientifico,
            "activa": True,
        }).execute()
        planta_id = planta_resp.data[0]["planta_id"]

    try:
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
    except Exception as e:
        msg = str(e).lower()
        if (
            "23505" in msg
            or "duplicate key" in msg
            or "unique constraint" in msg
            or "inventario_vivero_planta_altura" in msg
        ):
            nombre = req.nombre_comun or "esta planta"
            altura = req.altura_cm
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Ya tenés \"{nombre}\" de {altura} cm en tu inventario. "
                    f"Para subir otra variante cambiá la altura (ej: {altura + 10} cm). "
                    f"Si querés actualizar stock o precio, editá el item existente "
                    f"con el botón ✏️ desde 'Mi Inventario'."
                ),
            )
        raise HTTPException(500, detail=f"Error al guardar el inventario: {str(e)[:200]}")

    return {
        "ok": True,
        "inventario_id": inv_resp.data[0]["inventario_id"],
        "planta_id": planta_id,
    }


# ─────────────────── ACTUALIZAR INVENTARIO ───────────────────

ESTADOS_INVENTARIO = ("disponible", "agotado", "reservado", "en_crecimiento")


class ActualizarInventarioRequest(BaseModel):
    """Campos editables de un item del inventario (todos opcionales)."""
    nombre_comun: Optional[str] = Field(default=None, min_length=1, max_length=200)
    stock: Optional[int] = Field(default=None, ge=0)
    precio_mayorista: Optional[float] = Field(default=None, ge=0)
    precio_detal: Optional[float] = Field(default=None, ge=0)
    estado_planta: Optional[str] = Field(default=None)
    notas: Optional[str] = Field(default=None, max_length=1000)
    # ── Fase editor de tier (5 ago 2026) ──
    # tier_manual acepta: "S", "M", "L", "XL", "" (vacío = restaurar automático)
    # o None (no lo modifica).
    tier_manual: Optional[str] = Field(default=None)


@router.patch("/inventario/{inventario_id}")
async def actualizar_inventario(
    inventario_id: int,
    req: ActualizarInventarioRequest,
    user: UserContext = Depends(require_viverista),
):
    """Actualiza campos de un item del inventario.

    El nombre_comun vive en la tabla `plantas`, no en `inventario` — se
    actualiza por separado. Cada item de inventario tiene su propio
    planta_id (no se comparte entre viveros), así que renombrar es seguro
    y no afecta el catálogo de otros viveros.
    """
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    if req.estado_planta is not None and req.estado_planta not in ESTADOS_INVENTARIO:
        raise HTTPException(
            400,
            detail=f"Estado inválido. Válidos: {', '.join(ESTADOS_INVENTARIO)}",
        )

    if req.nombre_comun is not None and not req.nombre_comun.strip():
        raise HTTPException(400, detail="El nombre no puede estar vacío")

    db = admin()

    # Verificar que el item pertenece al vivero del usuario
    existing = db.table("inventario").select("vivero_id, planta_id").eq(
        "inventario_id", inventario_id
    ).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, detail="Item no encontrado")
    if existing.data[0]["vivero_id"] != user.vivero_id:
        raise HTTPException(403, detail="Este item no pertenece a tu vivero")

    planta_id = existing.data[0]["planta_id"]

    # Si viene nombre_comun, se actualiza en la tabla `plantas`
    nombre_actualizado = None
    if req.nombre_comun is not None:
        nuevo_nombre = req.nombre_comun.strip()
        db.table("plantas").update({
            "nombre_comun": nuevo_nombre,
        }).eq("planta_id", planta_id).execute()
        nombre_actualizado = nuevo_nombre

    # Resto de campos van en `inventario`
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

    # ── Fase editor de tier (5 ago 2026) ──
    # tier_manual: valida y setea override manual. El trigger auto_set_logistics_tier
    # de la BD sincroniza logistics_tier automáticamente al UPDATE.
    #   - "S", "M", "L", "XL" → override manual
    #   - "" (string vacío) → restaurar automático (limpia el override)
    #   - None → no lo tocamos (comportamiento anterior)
    if req.tier_manual is not None:
        if req.tier_manual == "":
            payload["tier_manual"] = None  # limpia override → trigger recalcula por altura
        elif req.tier_manual in ("S", "M", "L", "XL"):
            payload["tier_manual"] = req.tier_manual
        else:
            raise HTTPException(
                400,
                detail="tier_manual debe ser S/M/L/XL o vacío para automático"
            )

    if not payload and nombre_actualizado is None:
        raise HTTPException(400, detail="No hay campos para actualizar")

    if payload:
        resp = db.table("inventario").update(payload).eq(
            "inventario_id", inventario_id
        ).execute()
        if not resp.data:
            raise HTTPException(500, detail="No se pudo actualizar el inventario")

    actualizado = dict(payload)
    if nombre_actualizado is not None:
        actualizado["nombre_comun"] = nombre_actualizado

    return {
        "ok": True,
        "inventario_id": inventario_id,
        "actualizado": actualizado,
    }


# ─────────────────── ELIMINAR INVENTARIO ───────────────────

@router.delete("/inventario/{inventario_id}")
async def eliminar_inventario(
    inventario_id: int,
    user: UserContext = Depends(require_viverista),
):
    """Elimina un item del inventario."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    db = admin()

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
