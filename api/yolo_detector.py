"""
[DEPRECATED] ViveroOnline — Detector de Plantas con YOLO-11 vía Ultralytics HUB
==============================================================================

⚠️  NO USAR. Implementación dormida.

La implementación ACTIVA de YOLO vive en `app/services/yolo.py`
(importada por `app/routes/catalogo.py`). Si necesitás tocar la
detección de plantas, hacelo allá — NO acá.

Este archivo quedó como deuda técnica de una arquitectura anterior
que usaba Ultralytics HUB (cloud API). Se conserva por dos razones:
  1. Histórica: documenta el approach cloud-API por si en el futuro
     se decide volver a él (p.ej., para offload de cómputo).
  2. Referencia: el flujo de bounding box + recorte con margen del
     10% sigue siendo válido como patrón si se reescribe.

Borrar en el próximo PR que toque la lógica de YOLO (upgrade de
modelo, nuevas clases de detección, cambios en crop_plant, etc).

────────────────────────────────────────────────────────────────────
Documentación original (para referencia):

Usa la API de Ultralytics HUB (cloud) para detección — sin instalar PyTorch.
YOLO-11 detecta y recorta la planta antes de pasarla a Gemini Vision.

Flujo:
  imagen_original → YOLO-11 detecta planta → recorta región → Gemini identifica

Nota: Si YOLO falla, Gemini actúa solo (fallback automático).
"""

import base64
import os
from io import BytesIO
from typing import Optional, Tuple

import httpx
from PIL import Image

ULTRALYTICS_API_KEY = os.environ.get("ULTRALYTICS_API_KEY", "")
ULTRALYTICS_MODEL   = os.environ.get("ULTRALYTICS_MODEL_ID", "")  # ID del modelo en Ultralytics HUB


async def detectar_y_recortar_planta(
    imagen_bytes: bytes,
    confianza_min: float = 0.35,
) -> Tuple[bytes, dict]:
    """
    Usa YOLO-11 vía Ultralytics HUB API para detectar la planta principal.
    Devuelve (imagen_recortada, info_deteccion).
    Si falla, devuelve la imagen original sin recortar.
    """
    info = {
        "yolo_activo": False,
        "confianza":   0.0,
        "clase":       "planta",
        "bbox":        None,
    }

    # Si no hay API key de Ultralytics, usar imagen completa
    if not ULTRALYTICS_API_KEY or not ULTRALYTICS_MODEL:
        return imagen_bytes, info

    try:
        img_b64 = base64.b64encode(imagen_bytes).decode()

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://api.ultralytics.com/v1/predict/{ULTRALYTICS_MODEL}",
                headers={"x-api-key": ULTRALYTICS_API_KEY},
                json={
                    "imgsz": 640,
                    "conf":  confianza_min,
                    "iou":   0.45,
                    "image": img_b64,
                },
            )

        if r.status_code != 200:
            return imagen_bytes, info

        data = r.json()
        predicciones = data.get("data", [])

        if not predicciones:
            return imagen_bytes, info

        # Tomar la detección con mayor confianza
        mejor = max(predicciones, key=lambda x: x.get("confidence", 0))

        if mejor.get("confidence", 0) < confianza_min:
            return imagen_bytes, info

        # Recortar la imagen según el bounding box
        bbox = mejor.get("box", {})
        x1 = int(bbox.get("x1", 0))
        y1 = int(bbox.get("y1", 0))
        x2 = int(bbox.get("x2", 0))
        y2 = int(bbox.get("y2", 0))

        if x2 > x1 and y2 > y1:
            img = Image.open(BytesIO(imagen_bytes))
            # Agregar margen del 10% alrededor del objeto detectado
            margin_x = int((x2 - x1) * 0.10)
            margin_y = int((y2 - y1) * 0.10)
            x1 = max(0, x1 - margin_x)
            y1 = max(0, y1 - margin_y)
            x2 = min(img.width,  x2 + margin_x)
            y2 = min(img.height, y2 + margin_y)

            recortada = img.crop((x1, y1, x2, y2))
            buf = BytesIO()
            recortada.save(buf, format="JPEG", quality=90)
            img_recortada = buf.getvalue()

            info["yolo_activo"] = True
            info["confianza"]   = round(mejor.get("confidence", 0), 2)
            info["clase"]       = mejor.get("name", "planta")
            info["bbox"]        = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

            return img_recortada, info

    except Exception as e:
        print(f"YOLO-11 no disponible: {e} — usando imagen completa")

    return imagen_bytes, info


def comprimir_imagen(imagen_bytes: bytes, max_px: int = 800, calidad: int = 85) -> bytes:
    """Comprime y redimensiona la imagen antes del análisis."""
    try:
        img = Image.open(BytesIO(imagen_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.width > max_px or img.height > max_px:
            img.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=calidad)
        return buf.getvalue()
    except Exception:
        return imagen_bytes
