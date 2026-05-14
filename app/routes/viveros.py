"""Endpoints del perfil del vivero (para viveristas autenticados).

- GET    /api/viveros/me              → datos del vivero del usuario actual
- PATCH  /api/viveros/me              → actualiza historia + dirección
- POST   /api/viveros/me/foto         → sube/reemplaza foto principal (portada)
- POST   /api/viveros/me/fotos        → agrega una foto a la galería (máx 3)
- DELETE /api/viveros/me/fotos        → elimina una foto de la galería por índice
"""
from __future__ import annotations
import io
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from PIL import Image

from app.auth.deps import UserContext, require_viverista
from app.services.supabase import admin


router = APIRouter(prefix="/api/viveros", tags=["viveros"])


MAX_GALERIA_FOTOS = 3
BUCKET_VIVEROS = "viveros-fotos"


# ─────────────────── SCHEMAS ───────────────────

class ViveroPerfil(BaseModel):
    """Perfil completo del vivero (lo que ve el viverista en su dashboard)."""
    vivero_id: int
    nombre_vivero: str
    propietario: Optional[str] = None
    ciudad: Optional[str] = None
    departamento: Optional[str] = None
    direccion: Optional[str] = None
    telefono: Optional[str] = None
    whatsapp_numero: Optional[str] = None
    historia: Optional[str] = None
    foto_url: Optional[str] = None
    fotos_galeria: list[str] = Field(default_factory=list)
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    estado: Optional[str] = None


class ActualizarPerfilRequest(BaseModel):
    """Campos editables del perfil del vivero (todos opcionales)."""
    historia: Optional[str] = Field(default=None, max_length=5000)
    direccion: Optional[str] = Field(default=None, max_length=500)


# ─────────────────── HELPERS ───────────────────

def _normalizar_imagen_jpeg(raw: bytes) -> bytes:
    """Convierte cualquier formato a JPEG, max 1600px lado mayor, calidad 85."""
    try:
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        max_side = 1600
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()
    except Exception as e:
        raise HTTPException(400, detail=f"Imagen inválida: {e}")


def _extraer_path_storage(url: str) -> Optional[str]:
    """De una URL pública del bucket viveros-fotos extrae el path interno.
    
    Ejemplo:
    https://...supabase.co/storage/v1/object/public/viveros-fotos/3/galeria-abc.jpg
    → '3/galeria-abc.jpg'
    """
    marker = f"/{BUCKET_VIVEROS}/"
    if marker in url:
        return url.split(marker, 1)[1]
    return None


# ─────────────────── GET PERFIL ───────────────────

@router.get("/me", response_model=ViveroPerfil)
async def obtener_perfil_vivero(user: UserContext = Depends(require_viverista)):
    """Devuelve el perfil completo del vivero del viverista actual."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    db = admin()
    resp = db.table("viveros").select(
        "vivero_id, nombre_vivero, propietario, ciudad, departamento, direccion, "
        "telefono, whatsapp_numero, historia, foto_url, fotos_galeria, "
        "latitud, longitud, estado"
    ).eq("vivero_id", user.vivero_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Vivero no encontrado")

    row = resp.data[0]
    # fotos_galeria viene como JSONB; supabase-py lo devuelve como list/dict
    galeria = row.get("fotos_galeria") or []
    if not isinstance(galeria, list):
        galeria = []

    return ViveroPerfil(
        vivero_id=row["vivero_id"],
        nombre_vivero=row.get("nombre_vivero") or "",
        propietario=row.get("propietario"),
        ciudad=row.get("ciudad"),
        departamento=row.get("departamento"),
        direccion=row.get("direccion"),
        telefono=row.get("telefono"),
        whatsapp_numero=row.get("whatsapp_numero"),
        historia=row.get("historia"),
        foto_url=row.get("foto_url"),
        fotos_galeria=[str(u) for u in galeria if u],
        latitud=float(row["latitud"]) if row.get("latitud") else None,
        longitud=float(row["longitud"]) if row.get("longitud") else None,
        estado=row.get("estado"),
    )


# ─────────────────── PATCH PERFIL ───────────────────

@router.patch("/me")
async def actualizar_perfil_vivero(
    req: ActualizarPerfilRequest,
    user: UserContext = Depends(require_viverista),
):
    """Actualiza historia y/o dirección del vivero."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    payload: dict = {}
    if req.historia is not None:
        payload["historia"] = req.historia.strip() or None
    if req.direccion is not None:
        payload["direccion"] = req.direccion.strip() or None

    if not payload:
        raise HTTPException(400, detail="No hay campos para actualizar")

    db = admin()
    resp = db.table("viveros").update(payload).eq(
        "vivero_id", user.vivero_id
    ).execute()

    if not resp.data:
        raise HTTPException(500, detail="No se pudo actualizar el perfil")

    return {"ok": True, "vivero_id": user.vivero_id, "actualizado": payload}


# ─────────────────── POST FOTO PRINCIPAL ───────────────────

@router.post("/me/foto")
async def subir_foto_vivero(
    imagen: UploadFile = File(...),
    user: UserContext = Depends(require_viverista),
):
    """Sube/reemplaza la foto principal (portada) del vivero."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    if not imagen.content_type or not imagen.content_type.startswith("image/"):
        raise HTTPException(400, detail="Debe ser una imagen")

    raw = await imagen.read()
    if len(raw) == 0:
        raise HTTPException(400, detail="Imagen vacía")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, detail="Imagen muy grande (máx 10MB)")

    image_bytes = _normalizar_imagen_jpeg(raw)

    db = admin()
    filename = f"{user.vivero_id}/portada-{uuid.uuid4().hex[:8]}.jpg"
    try:
        db.storage.from_(BUCKET_VIVEROS).upload(
            path=filename,
            file=image_bytes,
            file_options={"content-type": "image/jpeg"},
        )
        public_url = db.storage.from_(BUCKET_VIVEROS).get_public_url(filename)
    except Exception as e:
        raise HTTPException(500, detail=f"Error al subir imagen: {e}")

    # Update foto_url en la tabla viveros
    db.table("viveros").update({"foto_url": public_url}).eq(
        "vivero_id", user.vivero_id
    ).execute()

    return {"ok": True, "foto_url": public_url}


# ─────────────────── POST FOTO GALERÍA ───────────────────

@router.post("/me/fotos")
async def agregar_foto_galeria(
    imagen: UploadFile = File(...),
    user: UserContext = Depends(require_viverista),
):
    """Agrega una foto a la galería del vivero (máx 3 fotos adicionales).

    Si ya hay 3 fotos, devuelve 409 Conflict.
    """
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    if not imagen.content_type or not imagen.content_type.startswith("image/"):
        raise HTTPException(400, detail="Debe ser una imagen")

    raw = await imagen.read()
    if len(raw) == 0:
        raise HTTPException(400, detail="Imagen vacía")
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, detail="Imagen muy grande (máx 10MB)")

    db = admin()

    # Leer galería actual
    current_resp = db.table("viveros").select("fotos_galeria").eq(
        "vivero_id", user.vivero_id
    ).limit(1).execute()
    if not current_resp.data:
        raise HTTPException(404, detail="Vivero no encontrado")

    galeria = current_resp.data[0].get("fotos_galeria") or []
    if not isinstance(galeria, list):
        galeria = []

    if len(galeria) >= MAX_GALERIA_FOTOS:
        raise HTTPException(
            409,
            detail=f"Ya tenés el máximo de {MAX_GALERIA_FOTOS} fotos en la galería. Eliminá una antes de agregar otra.",
        )

    image_bytes = _normalizar_imagen_jpeg(raw)

    filename = f"{user.vivero_id}/galeria-{uuid.uuid4().hex[:8]}.jpg"
    try:
        db.storage.from_(BUCKET_VIVEROS).upload(
            path=filename,
            file=image_bytes,
            file_options={"content-type": "image/jpeg"},
        )
        public_url = db.storage.from_(BUCKET_VIVEROS).get_public_url(filename)
    except Exception as e:
        raise HTTPException(500, detail=f"Error al subir imagen: {e}")

    # Append a la galería
    galeria.append(public_url)
    db.table("viveros").update({"fotos_galeria": galeria}).eq(
        "vivero_id", user.vivero_id
    ).execute()

    return {
        "ok": True,
        "foto_url": public_url,
        "fotos_galeria": galeria,
        "total": len(galeria),
    }


# ─────────────────── DELETE FOTO GALERÍA ───────────────────

@router.delete("/me/fotos")
async def eliminar_foto_galeria(
    index: int = Query(..., ge=0, description="Índice de la foto a eliminar (0-based)"),
    user: UserContext = Depends(require_viverista),
):
    """Elimina una foto de la galería por su índice (0-based)."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    db = admin()

    current_resp = db.table("viveros").select("fotos_galeria").eq(
        "vivero_id", user.vivero_id
    ).limit(1).execute()
    if not current_resp.data:
        raise HTTPException(404, detail="Vivero no encontrado")

    galeria = current_resp.data[0].get("fotos_galeria") or []
    if not isinstance(galeria, list):
        galeria = []

    if index >= len(galeria):
        raise HTTPException(404, detail=f"No hay foto en el índice {index}")

    # Extraer URL a eliminar y borrarla del array
    url_eliminada = galeria.pop(index)

    # Update DB
    db.table("viveros").update({"fotos_galeria": galeria}).eq(
        "vivero_id", user.vivero_id
    ).execute()

    # Best-effort: borrar del bucket también (no rompemos si falla)
    try:
        path = _extraer_path_storage(url_eliminada)
        if path:
            db.storage.from_(BUCKET_VIVEROS).remove([path])
    except Exception:
        pass

    return {
        "ok": True,
        "eliminada": url_eliminada,
        "fotos_galeria": galeria,
        "total": len(galeria),
    }
