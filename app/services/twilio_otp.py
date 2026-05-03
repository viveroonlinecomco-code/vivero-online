"""Servicio de Twilio Verify para OTP vía WhatsApp.

Usa Twilio Verify API (no manejo manual de OTP):
- Twilio genera, envía, expira y valida el código
- https://www.twilio.com/docs/verify/api
"""
from __future__ import annotations
from typing import Literal

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from app.config import get_settings


class TwilioOtpService:
    def __init__(self) -> None:
        s = get_settings()
        self._client = Client(s.twilio_account_sid, s.twilio_auth_token)
        self._service_sid = s.twilio_verify_service_sid

    def send(self, whatsapp: str) -> tuple[bool, str]:
        """Envía OTP por WhatsApp. Retorna (ok, status|error)."""
        try:
            v = self._client.verify.v2.services(self._service_sid).verifications.create(
                to=whatsapp,
                channel="whatsapp",
            )
            return v.status == "pending", v.status
        except TwilioRestException as e:
            return False, f"twilio_error:{e.code}:{e.msg}"

    def check(self, whatsapp: str, code: str) -> tuple[bool, str]:
        """Valida el OTP. Retorna (ok, status)."""
        try:
            vc = self._client.verify.v2.services(
                self._service_sid
            ).verification_checks.create(to=whatsapp, code=code)
            return vc.status == "approved", vc.status
        except TwilioRestException as e:
            return False, f"twilio_error:{e.code}:{e.msg}"


# Singleton simple
_instance: TwilioOtpService | None = None


def get_otp_service() -> TwilioOtpService:
    global _instance
    if _instance is None:
        _instance = TwilioOtpService()
    return _instance
