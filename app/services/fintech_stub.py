"""Stub genérico para integración con fintech B2B (crédito 30/60/90d).

Diseño intencional: este módulo NO menciona a ningún proveedor específico.
Cuando ViveroOnline contrate una fintech real (Kontempo, Addi, Klym, u otra),
solo se implementan las funciones concretas manteniendo la misma interfaz.

Uso típico desde precios.py o pedidos.py:
    from app.services.fintech_stub import verificar_linea_credito

    if verificar_linea_credito(cliente_id, monto_solicitado, plazo_dias=30):
        # crear pedido con plazo 30d
        ...
    else:
        # forzar pago inmediato
        ...

Estado actual (21 jul 2026): fintech_activa=false en configuracion_global.
Todos los métodos retornan valores seguros (False, None) hasta que se contrate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.config_global import get_config

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# TIPOS DE DATOS (contratos estables — no cambian al conectar proveedor real)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class LineaCredito:
    """Información de la línea de crédito de un cliente en la fintech."""
    cliente_id: int
    aprobada: bool
    monto_maximo_cop: float
    monto_disponible_cop: float
    plazos_disponibles: list[int]  # ej: [30, 60, 90]
    fecha_evaluacion: datetime
    proveedor: str  # nombre del proveedor cuando se conecte


@dataclass
class SolicitudCredito:
    """Resultado de solicitar crédito para un pedido específico."""
    ok: bool
    referencia_fintech: Optional[str]
    monto_aprobado_cop: float
    plazo_dias: int
    fecha_pago_esperada: Optional[datetime]
    motivo_rechazo: Optional[str]
    payload_completo: dict[str, Any]


# ═══════════════════════════════════════════════════════════════════════
# API PÚBLICA — funciones que llama el resto del sistema
# ═══════════════════════════════════════════════════════════════════════

def fintech_esta_activa() -> bool:
    """Chequea la clave global fintech_activa en configuracion_global.

    Mientras esto sea False, verificar_linea_credito y solicitar_credito
    retornan valores seguros (rechazo) sin llamar a ningún proveedor externo.
    """
    return bool(get_config("fintech_activa", default=False))


def obtener_partner_actual() -> str:
    """Devuelve el nombre del proveedor fintech configurado.
    Vacío ('') si aún no se contrató.
    """
    return str(get_config("fintech_partner", default="") or "")


def verificar_linea_credito(
    cliente_id: int,
    monto_solicitado_cop: float,
    plazo_dias: int = 30,
) -> LineaCredito:
    """Verifica si un cliente tiene línea de crédito suficiente para un pedido.

    Args:
        cliente_id: ID del cliente en la BD de ViveroOnline
        monto_solicitado_cop: total del pedido a financiar
        plazo_dias: 30, 60 o 90 (default 30)

    Returns:
        LineaCredito con aprobada=False si fintech no está activa o si el
        proveedor rechaza. NUNCA levanta excepción — siempre retorna algo usable.
    """
    if not fintech_esta_activa():
        return LineaCredito(
            cliente_id=cliente_id,
            aprobada=False,
            monto_maximo_cop=0.0,
            monto_disponible_cop=0.0,
            plazos_disponibles=[],
            fecha_evaluacion=datetime.now(timezone.utc),
            proveedor="",
        )

    # Cuando haya proveedor real, acá va la llamada HTTP correspondiente.
    # Ejemplo genérico:
    #
    #     partner = obtener_partner_actual()
    #     client = _get_fintech_client(partner)
    #     resp = await client.check_credit_line(cliente_id, monto, plazo)
    #     return LineaCredito(...)

    logger.info(
        f"verificar_linea_credito: fintech_activa=True pero sin implementación "
        f"conectada aún. Cliente {cliente_id}, monto {monto_solicitado_cop}, "
        f"plazo {plazo_dias}d. Retornando aprobada=False (fail-safe)."
    )

    return LineaCredito(
        cliente_id=cliente_id,
        aprobada=False,
        monto_maximo_cop=0.0,
        monto_disponible_cop=0.0,
        plazos_disponibles=[],
        fecha_evaluacion=datetime.now(timezone.utc),
        proveedor=obtener_partner_actual(),
    )


def solicitar_credito(
    cliente_id: int,
    monto_cop: float,
    plazo_dias: int,
    transaccion_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> SolicitudCredito:
    """Solicita un crédito real para un pedido específico.

    Se llama después de verificar_linea_credito y de confirmar el pedido.
    El proveedor emite una referencia que se guarda en pagos.referencia_externa.

    Args:
        cliente_id: ID del cliente
        monto_cop: monto exacto del pedido
        plazo_dias: 30, 60 o 90
        transaccion_id: ID de la transaccion_b2b vinculada (para trazabilidad)
        metadata: dict opcional para adjuntar contexto adicional al proveedor

    Returns:
        SolicitudCredito con ok=False si fintech no está activa o si el
        proveedor rechaza. NUNCA levanta excepción.
    """
    if not fintech_esta_activa():
        return SolicitudCredito(
            ok=False,
            referencia_fintech=None,
            monto_aprobado_cop=0.0,
            plazo_dias=plazo_dias,
            fecha_pago_esperada=None,
            motivo_rechazo="fintech_inactiva",
            payload_completo={},
        )

    # Cuando haya proveedor real, acá va la llamada HTTP correspondiente.
    # Ejemplo genérico:
    #
    #     partner = obtener_partner_actual()
    #     client = _get_fintech_client(partner)
    #     resp = await client.request_credit(
    #         cliente_id=cliente_id,
    #         amount=monto_cop,
    #         term_days=plazo_dias,
    #         reference=str(transaccion_id) if transaccion_id else "",
    #         metadata=metadata or {},
    #     )
    #     return SolicitudCredito(
    #         ok=resp.approved,
    #         referencia_fintech=resp.reference_id,
    #         ...
    #     )

    logger.info(
        f"solicitar_credito: fintech_activa=True pero sin implementación "
        f"conectada aún. Cliente {cliente_id}, monto {monto_cop}, "
        f"plazo {plazo_dias}d. Retornando ok=False (fail-safe)."
    )

    return SolicitudCredito(
        ok=False,
        referencia_fintech=None,
        monto_aprobado_cop=0.0,
        plazo_dias=plazo_dias,
        fecha_pago_esperada=None,
        motivo_rechazo="proveedor_no_conectado",
        payload_completo={},
    )


def procesar_webhook_fintech(payload: dict[str, Any]) -> dict[str, Any]:
    """Punto de entrada para webhooks del proveedor (aprobación, pago recibido, etc).

    Se invoca desde app/routes/fintech.py cuando llega un webhook.
    Cuando haya proveedor real, acá se valida la firma HMAC y se actualiza
    la tabla pagos con el nuevo estado.

    Args:
        payload: cuerpo JSON del webhook

    Returns:
        dict con {ok, mensaje, accion_tomada}
    """
    if not fintech_esta_activa():
        logger.warning(
            f"Webhook fintech recibido pero fintech_activa=false. "
            f"Payload ignorado. Keys: {list(payload.keys())}"
        )
        return {
            "ok": False,
            "mensaje": "fintech inactiva — webhook ignorado",
            "accion_tomada": "ninguna",
        }

    # Cuando haya proveedor real:
    #   1. Validar firma HMAC según el proveedor
    #   2. Extraer evento (approved / paid / rejected / defaulted)
    #   3. Actualizar tabla pagos con el nuevo estado
    #   4. Notificar al viverista si corresponde

    logger.info(
        f"Webhook fintech recibido. Partner: {obtener_partner_actual()}. "
        f"Payload keys: {list(payload.keys())}. Sin implementación conectada."
    )

    return {
        "ok": False,
        "mensaje": "proveedor no conectado",
        "accion_tomada": "log_only",
    }
