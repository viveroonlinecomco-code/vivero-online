"""Servicio ePayco para procesamiento de pagos B2B (PSE + tarjetas).

Estrategia:
- Usamos ePayco Checkout (modal/redirect) para no manejar datos de tarjeta directamente (PCI).
- ePayco redirige al usuario después del pago a `response_url` (success page).
- ePayco notifica el estado real al `confirmation_url` (webhook) - es la fuente de verdad.

Docs: https://docs.epayco.co
"""
from __future__ import annotations
import hashlib
import time
from typing import Literal, Optional

import httpx
from pydantic import BaseModel

from app.config import get_settings


# ─────────────────── SCHEMAS INTERNOS ───────────────────

class CheckoutRequest(BaseModel):
    """Datos para iniciar un checkout ePayco."""
    transaccion_id: int
    monto_cop: int           # en pesos (ePayco recibe entero, no decimal)
    descripcion: str
    nombre_cliente: str
    email_cliente: Optional[str] = None
    telefono_cliente: Optional[str] = None
    moneda: Literal["COP"] = "COP"


class CheckoutResponse(BaseModel):
    """URL hacia donde redirigir al usuario para completar el pago."""
    ok: bool
    checkout_url: str
    referencia: str
    factura: str            # ePayco "invoice" - lo usamos para reconciliar


class EpaycoConfirmation(BaseModel):
    """Payload típico que ePayco envía al webhook de confirmación."""
    x_id_factura: str           # nuestra factura/referencia
    x_id_invoice: Optional[str] = None
    x_ref_payco: str            # ID interno ePayco
    x_amount: str
    x_currency_code: str = "COP"
    x_response: str             # "Aceptada" | "Rechazada" | "Pendiente" | "Fallida"
    x_response_reason_text: Optional[str] = None
    x_transaction_id: str
    x_signature: str            # firma para validación


# ─────────────────── SERVICIO ───────────────────

class EpaycoService:
    """Cliente para ePayco Checkout API."""

    # Endpoints (sandbox vs producción se diferencian por flag TEST en checkout)
    BASE_URL = "https://secure.epayco.co"

    def __init__(self) -> None:
        s = get_settings()
        self._public_key = (s.epayco_public_key or "").strip()
        self._private_key = (s.epayco_private_key or "").strip()
        self._p_cust_id = (s.epayco_p_cust_id or "").strip()
        self._p_key = (s.epayco_p_key or "").strip()
        self._test_mode = not s.is_production

    @property
    def is_configured(self) -> bool:
        return bool(self._public_key and self._private_key)

    def build_checkout_payload(self, req: CheckoutRequest, response_url: str, confirmation_url: str) -> dict:
        """Construye el payload para el SDK de ePayco Checkout (modo redirect).

        El frontend incluye el script de ePayco y dispara el modal con este payload.
        Alternativamente se puede redirigir a una URL pre-firmada.
        """
        referencia = f"VO-{req.transaccion_id}-{int(time.time())}"
        return {
            "key": self._public_key,
            "test": "true" if self._test_mode else "false",
            "name": req.descripcion[:120],
            "description": req.descripcion[:255],
            "currency": req.moneda,
            "amount": str(req.monto_cop),
            "tax_base": "0",
            "tax": "0",
            "country": "CO",
            "lang": "es",
            "external": "false",
            "extra1": str(req.transaccion_id),
            "invoice": referencia,
            "name_billing": req.nombre_cliente[:80],
            "email_billing": req.email_cliente or "",
            "mobilephone_billing": req.telefono_cliente or "",
            "response": response_url,
            "confirmation": confirmation_url,
            "method_confirmation": "POST",
        }

    def validate_signature(self, conf: EpaycoConfirmation) -> bool:
        """Valida la firma SHA256 de ePayco para asegurar que la confirmación es real.

        Fórmula oficial:
            sha256(p_cust_id + "^" + p_key + "^" + ref_payco + "^" + transaction_id + "^" + amount + "^" + currency)
        """
        if not self._p_cust_id or not self._p_key:
            # En dev sin keys configuradas, pasa
            return self._test_mode

        raw = f"{self._p_cust_id}^{self._p_key}^{conf.x_ref_payco}^{conf.x_transaction_id}^{conf.x_amount}^{conf.x_currency_code}"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return expected.lower() == conf.x_signature.lower()

    async def fetch_transaction_status(self, ref_payco: str) -> dict:
        """Verifica con la API de ePayco el estado real de una transacción.

        Útil para confirmar antes de marcar el pago como completo.
        """
        url = f"{self.BASE_URL}/validation/v1/reference/{ref_payco}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()


# ─────────────────── HELPERS DE ESTADO ───────────────────

def map_epayco_state_to_db(x_response: str) -> str:
    """Mapea el estado de ePayco al estado interno de pagos.

    Estados ePayco: Aceptada | Rechazada | Pendiente | Fallida | Reversada | Retenida
    Estados DB:     aprobado | rechazado | pendiente_pse | rechazado | reversado | retenido
    """
    m = (x_response or "").lower().strip()
    return {
        "aceptada": "aprobado",
        "rechazada": "rechazado",
        "pendiente": "pendiente_pse",
        "fallida": "rechazado",
        "reversada": "reversado",
        "retenida": "retenido",
    }.get(m, "pendiente_pse")


# Singleton
_instance: EpaycoService | None = None


def get_epayco() -> EpaycoService:
    global _instance
    if _instance is None:
        _instance = EpaycoService()
    return _instance
