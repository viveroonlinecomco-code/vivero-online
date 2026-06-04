"""Endpoint temporal para suscribir la app al webhook de WhatsApp producción.
BORRAR este archivo después de usarlo una vez.
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.auth.deps import UserContext, require_admin
from app.config import get_settings

router = APIRouter(prefix="/api/admin", tags=["setup"])

@router.post("/setup-whatsapp-webhook")
async def setup_whatsapp_webhook(user: UserContext = Depends(require_admin)):
    """Suscribe la app al webhook de la WABA de producción.
    Solo admins. Llamar UNA VEZ y luego borrar este archivo.
    """
    s = get_settings()
    token = s.meta_wa_access_token
    waba_id = s.meta_wa_business_account_id
    webhook_url = f"{s.app_base_url}/api/whatsapp/webhook"

    if not token:
        raise HTTPException(500, "META_WA_ACCESS_TOKEN no configurado")
    if not waba_id:
        raise HTTPException(500, "META_WA_BUSINESS_ACCOUNT_ID no configurado")

    results = {}

    # 1. Suscribir la app a la WABA de producción
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://graph.facebook.com/v25.0/{waba_id}/subscribed_apps",
            headers={"Authorization": f"Bearer {token}"},
        )
        results["subscribed_apps"] = r.json()

    # 2. Actualizar URL del webhook
    async with httpx.AsyncClient() as client:
        r2 = await client.post(
            f"https://graph.facebook.com/v25.0/{waba_id}/subscribed_apps",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "subscribed_fields": ["messages"],
            }
        )
        results["subscribed_fields"] = r2.json()

    return {
        "ok": True,
        "waba_id": waba_id,
        "webhook_url": webhook_url,
        "results": results,
    }
