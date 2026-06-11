"""Endpoints de mantenimiento periódico — llamados por Vercel Cron."""
from __future__ import annotations
import logging
from fastapi import APIRouter, Header, HTTPException
from app.services.supabase import admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cron", tags=["cron"])


@router.post("/vencer-cotizaciones")
async def vencer_cotizaciones(authorization: str = Header(default="")):
    """Vence cotizaciones expiradas. Llamado por Vercel Cron cada hora."""
    import os
    cron_secret = os.getenv("CRON_SECRET", "")
    if cron_secret and authorization != f"Bearer {cron_secret}":
        raise HTTPException(status_code=401, detail="No autorizado")

    db = admin()
    try:
        result = db.rpc("vencer_cotizaciones_expiradas").execute()
        total = result.data[0] if result.data else 0
        logger.info(f"Cron vencer_cotizaciones: {total} vencidas")

        # Notificar por WhatsApp a compradores cuyas cotizaciones vencieron
        _notificar_vencidas(db)

        return {"ok": True, "vencidas": total}
    except Exception as e:
        logger.error(f"Error en cron vencer_cotizaciones: {e}")
        return {"ok": False, "error": str(e)}


def _notificar_vencidas(db):
    """Notifica por WhatsApp a compradores de cotizaciones recién vencidas."""
    import os
    from app.services.whatsapp_meta import send_text_message
    import asyncio

    try:
        # Buscar cotizaciones vencidas en la última hora sin notificación
        resp = db.table("cotizaciones").select(
            "cotizacion_id, cliente_id, prompt_original, fecha_vencimiento"
        ).eq("estado", "vencida").gte(
            "fecha_vencimiento",
            "now() - interval '1 hour'"
        ).is_("notas_agente", None).limit(20).execute()

        for cot in resp.data or []:
            try:
                cliente = db.table("clientes").select("whatsapp_numero").eq(
                    "cliente_id", cot["cliente_id"]
                ).limit(1).execute()

                if cliente.data and cliente.data[0].get("whatsapp_numero"):
                    nombre = cot.get("prompt_original") or f"Cotización #{cot['cotizacion_id']}"
                    base_url = os.getenv("APP_BASE_URL", "https://app.viveroonline.com.co")
                    msg = (
                        f"⏰ *Cotización vencida — ViveroOnline*\n\n"
                        f"Tu cotización *{nombre}* venció sin completar el pago.\n\n"
                        f"Si todavía te interesa, podés solicitar una nueva cotización:\n"
                        f"{base_url}/marketplace"
                    )
                    asyncio.create_task(
                        send_text_message(cliente.data[0]["whatsapp_numero"], msg)
                    )
                    # Marcar como notificada
                    db.table("cotizaciones").update({
                        "notas_agente": "vencida_notificada"
                    }).eq("cotizacion_id", cot["cotizacion_id"]).execute()
            except Exception as e:
                logger.warning(f"No se pudo notificar cotización vencida {cot['cotizacion_id']}: {e}")
    except Exception as e:
        logger.warning(f"Error notificando vencidas: {e}")
