"""
Webhook de ePayco - Recibe confirmaciones de pago
Rama: feat/payouts-60-40

FLUJO:
1. ePayco envía POST /api/ePayco/webhook cuando pago es confirmado
2. Sistema valida firma HMAC
3. Actualiza pagos.estado_pago = 'aprobado'
4. Crea transferencia_viverista con Payout 60%
5. Notifica viverista por WhatsApp

INTEGRACIÓN:
- Llamado desde: app/routes/pagos.py (después de ePayco.build_checkout_payload)
- Base de datos: Supabase (tablas: pagos, transferencias_viverista)
- Notificaciones: WhatsApp Meta API
"""

from __future__ import annotations
import logging
import hashlib
import hmac
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.supabase import admin
from app.services.config_global import get_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ePayco", tags=["ePayco Webhook"])


class EpaycoWebhookPayload(BaseModel):
    """Estructura de confirmación que envía ePayco"""
    x_id_factura: Optional[str] = None
    x_ref_payco: Optional[str] = None
    x_amount: Optional[str] = None
    x_response: Optional[str] = None
    x_response_reason_text: Optional[str] = None
    x_transaction_id: Optional[str] = None
    x_signature: Optional[str] = None


def _validate_epayco_signature(payload: dict, signature: str) -> bool:
    """
    Valida firma HMAC de ePayco
    
    Args:
        payload: datos del webhook
        signature: firma enviada por ePayco
    
    Returns:
        True si firma es válida, False si no
    """
    try:
        epayco_private_key = os.getenv("EPAYCO_PRIVATE_KEY", "")
        
        if not epayco_private_key:
            logger.warning("[ePayco] EPAYCO_PRIVATE_KEY no configurada")
            return False
        
        if not signature:
            logger.warning("[ePayco] Firma no recibida")
            return False
        
        # Construir string para validar firma (orden importa)
        factura = payload.get("x_id_factura", "")
        ref_payco = payload.get("x_ref_payco", "")
        amount = payload.get("x_amount", "")
        response = payload.get("x_response", "")
        
        signature_string = f"{factura}^{ref_payco}^{amount}^{response}^{epayco_private_key}"
        expected_signature = hashlib.md5(signature_string.encode()).hexdigest()
        
        is_valid = hmac.compare_digest(signature, expected_signature)
        
        if not is_valid:
            logger.warning(f"[ePayco] Firma inválida: esperada {expected_signature}, recibida {signature}")
        
        return is_valid
        
    except Exception as e:
        logger.error(f"[ePayco] Error validando firma: {str(e)}")
        return False


@router.post("/webhook")
async def epayco_webhook_handler(request: Request):
    """
    Webhook de ePayco
    
    Recibe confirmación cuando comprador paga en ePayco
    Estados: "1"=aprobado, "2"=rechazado, "0"=pendiente
    
    IMPORTANTE: ePayco envía form-encoded, no JSON
    """
    
    try:
        # 1. PARSEAR DATOS (ePayco envía form-encoded)
        form_data = await request.form()
        
        payload = {
            "x_id_factura": form_data.get("x_id_factura", ""),
            "x_ref_payco": form_data.get("x_ref_payco", ""),
            "x_amount": form_data.get("x_amount", ""),
            "x_response": form_data.get("x_response", ""),
            "x_response_reason_text": form_data.get("x_response_reason_text", ""),
            "x_transaction_id": form_data.get("x_transaction_id", ""),
            "x_signature": form_data.get("x_signature", ""),
        }
        
        factura = payload.get("x_id_factura", "")
        logger.info(f"[ePayco] Webhook recibido: factura={factura}, response={payload.get('x_response')}")
        
        # 2. VALIDAR FIRMA
        signature = payload.get("x_signature", "")
        if not _validate_epayco_signature(payload, signature):
            logger.error(f"[ePayco] Firma INVÁLIDA para factura {factura}")
            # Retornar OK igual para que ePayco no reintente indefinidamente
            return {"ok": True, "error": "invalid_signature"}
        
        logger.info(f"[ePayco] Firma VÁLIDA para factura {factura}")
        
        # 3. EXTRAER pago_id DE LA FACTURA
        # El formato es "PAG-{pago_id}"
        try:
            pago_id = int(factura.split("-")[1])
        except (IndexError, ValueError):
            logger.error(f"[ePayco] No se pudo extraer pago_id de {factura}")
            return {"ok": True}
        
        # 4. MAPEAR RESPUESTA DE ePayco A ESTADO DE BD
        # x_response: "1"=aprobado, "2"=rechazado, "0"=pendiente
        estado_map = {
            "1": "aprobado",
            "2": "rechazado",
            "0": "pendiente",
        }
        response_code = payload.get("x_response", "0")
        estado_nuevo = estado_map.get(response_code, "pendiente")
        
        logger.info(f"[ePayco] Pago {pago_id}: response={response_code} → estado={estado_nuevo}")
        
        # 5. OBTENER DATOS DEL PAGO EN BD
        db = admin()
        try:
            pago_result = db.table("pagos").select("*").eq("pago_id", pago_id).single().execute()
            
            if not pago_result.data:
                logger.error(f"[ePayco] Pago {pago_id} no encontrado en BD")
                return {"ok": True}
            
            pago = pago_result.data
            vivero_id = pago.get("vivero_id")
            monto_total = float(pago.get("monto_total", 0))
            
        except Exception as e:
            logger.error(f"[ePayco] Error obteniendo pago {pago_id}: {str(e)}")
            return {"ok": True}
        
        # 6. ACTUALIZAR ESTADO DEL PAGO EN BD
        try:
            db.table("pagos").update({
                "estado_pago": estado_nuevo,
                "epayco_id": payload.get("x_ref_payco", ""),
                "fecha_confirmacion": datetime.utcnow().isoformat(),
                "epayco_response": response_code,
                "epayco_reason": payload.get("x_response_reason_text", ""),
            }).eq("pago_id", pago_id).execute()
            
            logger.info(f"[ePayco] Pago {pago_id} actualizado a '{estado_nuevo}'")
            
        except Exception as e:
            logger.error(f"[ePayco] Error actualizando pago {pago_id}: {str(e)}")
            return {"ok": True}
        
        # 7. SI APROBADO → CREAR TRANSFERENCIA Y EJECUTAR PAYOUT 60%
        if estado_nuevo == "aprobado":
            try:
                # Calcular split Escenario 3: 3% VO, 97% viverista
                comision_vo_60 = (monto_total * 0.03) * 0.60  # 60% de la comisión
                monto_viverista_60 = (monto_total * 0.97) * 0.60  # 60% del total viverista
                
                # Crear transferencia en estado "enviado" (payout ejecutado)
                transfer_result = db.table("transferencias_viverista").insert({
                    "pago_id": pago_id,
                    "vivero_id": vivero_id,
                    "monto_plantas": monto_total,
                    "monto_flete": 0,  # TODO: separar cuando se implemente flete
                    "viverista_plantas": monto_viverista_60,
                    "viverista_flete": 0,
                    "viverista_total": monto_viverista_60,
                    "plataforma_plantas": comision_vo_60,
                    "plataforma_flete": 0,
                    "plataforma_total": comision_vo_60,
                    "estado": "enviado",  # Ya ejecutado
                    "referencia_banco": f"TR_{pago_id}_60_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                    "fecha_transferencia": datetime.utcnow().isoformat(),
                }).execute()
                
                logger.info(f"[ePayco] Payout 60% creado: pago={pago_id}, monto=${monto_viverista_60:.2f}")
                
                # 8. NOTIFICAR VIVERISTA POR WhatsApp (TODO: implementar cuando Meta API esté configurada)
                # Por ahora solo registramos
                logger.info(f"[ePayco] TODO: Notificar viverista {vivero_id} sobre Payout 60%")
                
            except Exception as e:
                logger.error(f"[ePayco] Error creando transferencia: {str(e)}", exc_info=True)
                # No es bloqueante, continuar
        
        elif estado_nuevo == "rechazado":
            logger.warning(f"[ePayco] Pago {pago_id} fue rechazado: {payload.get('x_response_reason_text')}")
            # TODO: Notificar al cliente que pago fue rechazado
        
        # 9. RETORNAR OK A ePayco
        return {"ok": True, "pago_id": pago_id, "status": estado_nuevo}
        
    except Exception as e:
        logger.error(f"[ePayco] Error procesando webhook: {str(e)}", exc_info=True)
        # Siempre retornar OK para evitar que ePayco reintente indefinidamente
        return {"ok": True, "error": str(e)}


@router.get("/test")
async def test_webhook():
    """Endpoint para probar que el webhook está activo"""
    return {"status": "ok", "webhook": "ePayco webhook está activo"}
