"""Endpoints de mantenimiento periódico."""
from __future__ import annotations
import logging
import os
from fastapi import APIRouter, Header, HTTPException
from app.services.supabase import admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cron", tags=["cron"])


@router.post("/vencer-cotizaciones")
async def vencer_cotizaciones(authorization: str = Header(default="")):
    """Vence cotizaciones expiradas. Llamado por cron-job.org cada hora."""
    cron_secret = os.getenv("CRON_SECRET", "")
    if cron_secret and authorization != f"Bearer {cron_secret}":
        raise HTTPException(status_code=401, detail="No autorizado")

    db = admin()
    try:
        result = db.rpc("vencer_cotizaciones_expiradas").execute()
        total = result.data[0] if result.data else 0
        logger.info(f"Cron vencer_cotizaciones: {total} vencidas")
        return {"ok": True, "vencidas": total}
    except Exception as e:
        logger.error(f"Error en cron: {e}")
        return {"ok": False, "error": str(e)}
