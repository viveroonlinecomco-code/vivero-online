"""Endpoint para el cron diario de onboarding de viveristas.

Llamado por Vercel Cron Jobs una vez al día (8 AM Bogotá = 13:00 UTC).
Vercel envía automáticamente el header Authorization: Bearer <CRON_SECRET>.

MÉTODO HTTP: GET (Vercel Cron Jobs solo soporta GET, confirmado 07 jul 2026).
No usar POST — Vercel llama SIEMPRE con GET aunque el cron esté configurado
para un endpoint tipo POST, y devuelve 405 Method Not Allowed.

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

RECORDATORIO ANUAL SMLMV (nuevo — 20 jul 2026):
- El 15 de enero de cada año, este cron dispara un recordatorio por
  WhatsApp al admin para actualizar el SMLMV en configuracion_global.
- El mensaje va SOLO al número configurado en ADMIN_WHATSAPP_NOTIF
  (no a compradores, no a viveristas).
- Idempotente: si el admin ya actualizó el SMLMV en el año en curso,
  no se envía el recordatorio.
- Best-effort: si falla el recordatorio, NUNCA bloquea el cron principal
  de onboarding.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.services.supabase import admin
from app.services.onboarding_wa import procesar_hito_diario
from app.services.whatsapp_meta import send_text_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


# ─── NUEVO: Recordatorio anual SMLMV (15 de enero) ────────────────────────────

async def _recordatorio_smlmv_si_corresponde() -> dict | None:
    """Verifica si hoy es 15 de enero y envía recordatorio SOLO al admin
    (número configurado en ADMIN_WHATSAPP_NOTIF) para actualizar el SMLMV
    en configuracion_global.

    Este mensaje NO se envía a compradores ni a viveristas — es una
    notificación operativa exclusiva del administrador de la plataforma.

    - Solo dispara el 15 de enero (día = 15, mes = 1, hora Bogotá)
    - Idempotente: si el admin ya actualizó el SMLMV en el año en curso,
      no se envía el recordatorio
    - Best-effort: si falla, no interrumpe el cron principal de onboarding

    Returns:
        dict con {'enviado': bool, 'motivo': str} para logging, o None si
        no corresponde disparar hoy.
    """
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    # Ajuste a hora Bogotá (UTC-5) para determinar si es 15 de enero local
    ahora_bogota = now - timedelta(hours=5)

    # Solo el 15 de enero de cada año
    if ahora_bogota.month != 1 or ahora_bogota.day != 15:
        return None

    admin_numero = os.getenv("ADMIN_WHATSAPP_NOTIF", "").strip()
    if not admin_numero:
        logger.warning(
            "Recordatorio SMLMV: ADMIN_WHATSAPP_NOTIF no configurado — "
            "no se puede notificar"
        )
        return {"enviado": False, "motivo": "sin_admin_configurado"}

    try:
        # Leer el SMLMV actual y cuándo se actualizó por última vez
        db = admin()
        resp = db.table("configuracion_global").select(
            "valor, actualizado_en"
        ).eq("clave", "smlmv_actual").limit(1).execute()

        if not resp.data:
            logger.warning("Recordatorio SMLMV: clave smlmv_actual no existe en configuracion_global")
            return {"enviado": False, "motivo": "clave_no_existe"}

        valor_actual = resp.data[0].get("valor", "?")
        actualizado_en = resp.data[0].get("actualizado_en")

        # Si el SMLMV ya se actualizó este año, no molestar al admin
        if actualizado_en:
            fecha_actualizado = datetime.fromisoformat(
                actualizado_en.replace("Z", "+00:00")
            )
            if fecha_actualizado.year == ahora_bogota.year:
                logger.info(
                    f"SMLMV ya actualizado en {ahora_bogota.year} "
                    f"(el {fecha_actualizado.date()}), no enviar recordatorio"
                )
                return {"enviado": False, "motivo": "ya_actualizado_este_ano"}

        # Formatear el valor con puntos como separador de miles (formato COP)
        try:
            valor_formateado = f"${int(valor_actual):,}".replace(",", ".")
        except (ValueError, TypeError):
            valor_formateado = f"${valor_actual}"

        # Enviar recordatorio SOLO al admin
        mensaje = (
            "🔔 *Recordatorio anual — SMLMV*\n\n"
            f"Hoy es 15 de enero de {ahora_bogota.year}.\n\n"
            f"El SMLMV configurado sigue en *{valor_formateado} COP* "
            f"del año pasado.\n\n"
            "*Acción sugerida:*\n"
            "1. Buscar el decreto oficial del SMLMV para el año en curso "
            "(Ministerio del Trabajo / Banco de la República).\n"
            "2. Actualizar el valor en el dashboard admin → Configuración → "
            "`smlmv_actual`.\n\n"
            "Esto ajusta automáticamente el umbral de descuentos B2B "
            "(actualmente 5 × SMLMV)."
        )

        await send_text_message(admin_numero, mensaje)
        logger.info(f"Recordatorio SMLMV enviado a admin {admin_numero}")
        return {"enviado": True, "motivo": "ok"}

    except Exception as e:
        logger.error(f"Error enviando recordatorio SMLMV al admin: {e}")
        return {"enviado": False, "motivo": f"error: {str(e)[:100]}"}


# ─── Endpoint del cron diario ─────────────────────────────────────────────────

@router.get("/cron-diario")
async def cron_diario(authorization: Optional[str] = Header(default=None)):
    """Endpoint invocado por Vercel Cron Jobs (1x/día, 8am Bogotá).

    Vercel envía automáticamente:
        Authorization: Bearer ${CRON_SECRET}

    Returns:
        dict con resumen de la corrida: procesados, enviados, saltados, errores.
        Incluye también el resultado del recordatorio SMLMV si aplicó.
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

    # ─── NUEVO: Recordatorio anual SMLMV (15 de enero) ───
    # Best-effort — NUNCA bloquea el cron principal si falla
    smlmv_result = None
    try:
        smlmv_result = await _recordatorio_smlmv_si_corresponde()
        if smlmv_result:
            logger.info(f"Recordatorio SMLMV resultado: {smlmv_result}")
    except Exception as e:
        logger.error(f"Error en recordatorio SMLMV (ignorado, no bloquea cron): {e}")

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
            "smlmv_recordatorio": smlmv_result,
        }

    # ─── Procesar cada fila ───
    resultados = {
        "procesados": 0,
        "enviados": [],
        "saltados": 0,
        "errores": [],
        "smlmv_recordatorio": smlmv_result,
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
