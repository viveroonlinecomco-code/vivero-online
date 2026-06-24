"""Endpoint para el cron diario de onboarding de viveristas.

Llamado por Vercel Cron Jobs una vez al día (8 AM Bogotá = 13:00 UTC).
Vercel envía automáticamente el header Authorization: Bearer <CRON_SECRET>.

Lee filas pendientes de onboarding_viverista_hitos y procesa cada una
con onboarding_wa.procesar_hito_diario(), que envía el hito que
corresponda o saltea si no toca aún.

SEGURIDAD:
- Header Authorization: Bearer ${CRON_SECRET} obligatorio
- Si CRON_SECRET no está configurada en el entorno: rechaza todas las
  llamadas con 500 (fail-closed, NO fail-open).
- Si el header no coincide: 401.

ROBUSTEZ:
- LIMIT 50 filas por ejecución para evitar timeout de Vercel (5 min máx).
  Si en algún momento crecemos a >50 onboardings activos simultáneos,
  el cron diario procesa 50 y el resto reintenta al día siguiente
  (idempotencia garantizada por hito_X_enviado_at IS NULL).
- Errores por fila NO detienen el cron completo: cada fallo se loggea
  y se agrega al array `errores` del resumen.

OBSERVABILIDAD:
- Retorna JSON con resumen: procesados, enviados, saltados, errores.
- Logs estructurados por nivel (INFO en happy path, ERROR en fallos).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.services.supabase import admin
from app.services.onboarding_wa import procesar_hito_diario

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


@router.post("/cron-diario")
async def cron_diario(authorization: Optional[str] = Header(default=None)):
    """Endpoint invocado por Vercel Cron Jobs (1x/día, 8am Bogotá).

    Vercel envía automáticamente:
        Authorization: Bearer ${CRON_SECRET}

    Returns:
        dict con resumen de la corrida: procesados, enviados, saltados, errores.
    """
    # ─── Auth (fail-closed) ───
    expected_secret = os.getenv("CRON_SECRET", "")
    if not expected_secret:
        logger.error("cron-onboarding: CRON_SECRET no configurado, rechazando")
        raise HTTPException(status_code=500, detail="CRON_SECRET not configured")

    expected_header = f"Bearer {expected_secret}"
    if not authorization or authorization != expected_header:
        logger.warning("cron-onboarding: authorization inválida")
        raise HTTPException(status_code=401, detail="unauthorized")

    # ─── Leer filas pendientes ───
    try:
        resp = (
            admin().table("onboarding_viverista_hitos")
            .select(
                "vivero_id, fecha_dia_0, "
                "hito_1_enviado_at, hito_3_enviado_at, hito_7_enviado_at"
            )
            .eq("completado", False)
            .limit(50)  # tope por ejecución para evitar timeout
            .execute()
        )
        filas = resp.data or []
    except Exception as e:
        logger.error(f"cron-onboarding: error leyendo filas pendientes: {e}")
        raise HTTPException(status_code=500, detail="db error")

    if not filas:
        logger.info("cron-onboarding: sin filas pendientes")
        return {
            "procesados": 0,
            "enviados": [],
            "saltados": 0,
            "errores": [],
        }

    # ─── Procesar cada fila ───
    resultados = {
        "procesados": 0,
        "enviados": [],
        "saltados": 0,
        "errores": [],
    }

    for row in filas:
        try:
            result = await procesar_hito_diario(row)
            resultados["procesados"] += 1
            accion = result.get("accion")

            if accion == "enviado":
                resultados["enviados"].append({
                    "vivero_id": row["vivero_id"],
                    "hito": result.get("hito"),
                })
            elif accion == "saltado":
                resultados["saltados"] += 1
            elif accion == "error":
                resultados["errores"].append({
                    "vivero_id": row["vivero_id"],
                    "error": result.get("error"),
                })
        except Exception as e:
            # No detenemos el cron si una fila falla
            logger.error(
                f"cron-onboarding: excepción procesando vivero "
                f"{row.get('vivero_id')}: {e}"
            )
            resultados["errores"].append({
                "vivero_id": row.get("vivero_id"),
                "error": f"exception: {str(e)[:100]}",
            })

    logger.info(
        f"cron-onboarding completado: "
        f"procesados={resultados['procesados']}, "
        f"enviados={len(resultados['enviados'])}, "
        f"saltados={resultados['saltados']}, "
        f"errores={len(resultados['errores'])}"
    )

    return resultados
