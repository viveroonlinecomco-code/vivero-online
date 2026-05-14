"""Rutas de gestión del vivero del viverista logueado.

- GET   /api/viveros/me        → datos del vivero del usuario
- PATCH /api/viveros/me        → actualizar historia + dirección
- POST  /api/viveros/me/foto   → subir foto principal (bucket viveros-fotos)
"""
from __future__ import annotations
import io
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from PIL import Image

from app.auth.deps import UserContext, require_viverista
from app.services.supabase import admin


router = APIRouter(prefix="/api/viveros", tags=["viveros"])


# ─────────────────── SCHEMAS ───────────────────

class ViveroPerfil(BaseModel):
    """Datos del vivero que ve el viverista al editar su perfil."""
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
    nit: Optional[str] = None
    estado: str
    onboarding_completo: bool


class ActualizarPerfilRequest(BaseModel):
    """Campos editables vía PATCH /api/viveros/me."""
    historia: Optional[str] = Field(default=None, max_length=2000)
    direccion: Optional[str] = Field(default=None, max_length=500)


# ─────────────────── ENDPOINTS ───────────────────

@router.get("/me", response_model=ViveroPerfil)
async def obtener_mi_vivero(user: UserContext = Depends(require_viverista)):
    """Devuelve los datos del vivero del usuario actual."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    db = admin()
    resp = db.table("viveros").select(
        "vivero_id, nombre_vivero, propietario, ciudad, departamento, "
        "direccion, telefono, whatsapp_numero, historia, foto_url, "
        "nit, estado, onboarding_completo"
    ).eq("vivero_id", user.vivero_id).limit(1).execute()

    if not resp.data:
        raise HTTPException(404, detail="Vivero no encontrado")

    return ViveroPerfil(**resp.data[0])


@router.patch("/me", response_model=ViveroPerfil)
async def actualizar_mi_vivero(
    req: ActualizarPerfilRequest,
    user: UserContext = Depends(require_viverista),
):
    """Actualiza los campos editables del vivero (historia y dirección).

    Sólo modifica los campos enviados; los None se ignoran para no
    pisar valores existentes en DB.
    """
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
        raise HTTPException(500, detail="No se pudo actualizar el vivero")

    # Devolver el perfil completo actualizado
    return await obtener_mi_vivero(user)


@router.post("/me/foto")
async def subir_foto_vivero(
    imagen: UploadFile = File(...),
    user: UserContext = Depends(require_viverista),
):
    """Sube la foto principal del vivero al bucket viveros-fotos."""
    if not user.vivero_id:
        raise HTTPException(400, detail="Tu perfil no está vinculado a un vivero")

    # Validar tipo
    if not imagen.content_type or not imagen.content_type.startswith("image/"):
        raise HTTPException(400, detail="Debe ser una imagen")

    raw = await imagen.read()
    if len(raw) == 0:
        raise HTTPException(400, detail="Imagen vacía")
    if len(raw) > 10 * 1024 * 1024:  # 10 MB
        raise HTTPException(413, detail="Imagen muy grande (máx 10MB)")

    # Normalizar a JPEG (mismo patrón que /api/catalogo/identificar)
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

    # Subir al bucket viveros-fotos (path: vivero_id/portada-XXXXXXXX.jpg)
    db = admin()
    filename = f"{user.vivero_id}/portada-{uuid.uuid4().hex[:8]}.jpg"
    try:
        db.storage.from_("viveros-fotos").upload(
            path=filename,
            file=image_bytes,
            file_options={"content-type": "image/jpeg"},
        )
        public_url = db.storage.from_("viveros-fotos").get_public_url(filename)
    except Exception as e:
        raise HTTPException(500, detail=f"Error al subir imagen: {e}")

    # Guardar el nuevo foto_url en viveros
    db.table("viveros").update({"foto_url": public_url}).eq(
        "vivero_id", user.vivero_id
    ).execute()

    return {"ok": True, "foto_url": public_url}
