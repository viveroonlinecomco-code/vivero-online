"""Servicio YOLO-11 (Ultralytics HUB) para preprocesamiento de imágenes.

Estrategia:
- Si está configurado ULTRALYTICS_API_KEY: detecta bounding box de la planta y recorta.
- Si no: pasa la imagen original (fallback elegante).
- Si YOLO falla por timeout/red: log + pasa original (no rompe el flujo).

Por qué este preprocessing:
- Fotos de viveristas suelen tener fondos ruidosos (manos, etiquetas, otras plantas).
- Recortar a la planta dominante mejora la precisión de Gemini Vision en ~10-20%.
- También reduce tokens enviados a Gemini → menor costo.
"""
from __future__ import annotations
import io
from typing import Optional

import httpx
from PIL import Image

from app.config import get_settings


# Clases de COCO que consideramos "planta" para el modelo base de YOLO-11
# Si tienes un modelo custom entrenado en plantas, ajusta esta lista.
PLANT_CLASSES = {"potted plant", "plant", "vase"}

# Confianza mínima para aceptar la detección
MIN_CONFIDENCE = 0.35

# Padding alrededor del bounding box detectado (en %)
CROP_PADDING = 0.10


class YoloService:
    def __init__(self) -> None:
        s = get_settings()
        self._api_key = (s.ultralytics_api_key or "").strip()
        self._enabled = bool(self._api_key)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def crop_plant(self, image_bytes: bytes) -> tuple[bytes, dict]:
        """Detecta la planta dominante y recorta. Retorna (bytes_recortados, metadata).

        Si YOLO está desactivado o falla, retorna (image_bytes original, metadata vacía).
        Nunca lanza excepción — fallback elegante.
        """
        meta: dict = {"yolo_used": False, "confidence": None, "bbox": None}

        if not self._enabled:
            return image_bytes, meta

        try:
            detections = await self._detect(image_bytes)
        except Exception as e:
            meta["error"] = str(e)[:120]
            return image_bytes, meta

        # Filtrar solo clases de planta con suficiente confianza
        plant_dets = [
            d for d in detections
            if d.get("name", "").lower() in PLANT_CLASSES
            and d.get("confidence", 0) >= MIN_CONFIDENCE
        ]
        if not plant_dets:
            return image_bytes, meta

        # Elegir la detección de mayor confianza (planta dominante)
        best = max(plant_dets, key=lambda d: d.get("confidence", 0))
        bbox = best.get("box", {})
        if not all(k in bbox for k in ("x1", "y1", "x2", "y2")):
            return image_bytes, meta

        # Recortar con padding
        try:
            cropped = self._crop_with_padding(image_bytes, bbox, CROP_PADDING)
        except Exception as e:
            meta["error"] = f"crop_failed:{e}"[:120]
            return image_bytes, meta

        meta.update({
            "yolo_used": True,
            "confidence": best.get("confidence"),
            "bbox": bbox,
            "class": best.get("name"),
        })
        return cropped, meta

    async def _detect(self, image_bytes: bytes) -> list[dict]:
        """Llama a Ultralytics HUB API para detección.

        API: https://docs.ultralytics.com/hub/inference-api/
        """
        url = "https://predict.ultralytics.com"
        headers = {"x-api-key": self._api_key}
        files = {"image": ("plant.jpg", image_bytes, "image/jpeg")}
        # Modelo público base; si tienes uno custom, sustituye aquí
        data = {"model": "yolo11n.pt", "confidence": str(MIN_CONFIDENCE)}

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, headers=headers, files=files, data=data)
            r.raise_for_status()
            payload = r.json()

        # La respuesta tiene shape: {"images": [{"results": [...]}]}
        if isinstance(payload, dict):
            imgs = payload.get("images") or []
            if imgs and isinstance(imgs[0], dict):
                return imgs[0].get("results") or []
        return []

    def _crop_with_padding(self, image_bytes: bytes, bbox: dict, padding: float) -> bytes:
        """Recorta la imagen al bounding box con un padding relativo."""
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size

        x1, y1 = float(bbox["x1"]), float(bbox["y1"])
        x2, y2 = float(bbox["x2"]), float(bbox["y2"])

        bw = x2 - x1
        bh = y2 - y1
        px = bw * padding
        py = bh * padding

        # Aplicar padding y clamp a los bordes
        nx1 = max(0, int(x1 - px))
        ny1 = max(0, int(y1 - py))
        nx2 = min(w, int(x2 + px))
        ny2 = min(h, int(y2 + py))

        cropped = img.crop((nx1, ny1, nx2, ny2))

        # Asegurar RGB y guardar como JPEG optimizado
        if cropped.mode != "RGB":
            cropped = cropped.convert("RGB")
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()


# Singleton
_instance: YoloService | None = None


def get_yolo() -> YoloService:
    global _instance
    if _instance is None:
        _instance = YoloService()
    return _instance
