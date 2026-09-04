"""Rutas de pagos vía ePayco.

Fase 4 (21 jul 2026): eliminado el hardcoded `monto_plataforma = monto_cop * 0.05`.
El monto_plataforma ahora se lee de la transaccion_b2b correspondiente
(que YA guardó el desglose calculado con el motor matricial de precios.py).
Si por alguna razón no está disponible, se recalcula al vuelo desde el modelo.

Fase 10.4 (24 ago 2026): Agregado descuento B2B temporal 12% + ePayco
(reversible automáticamente cuando fintech_activa=true en configuracion_global).
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.deps import UserContext, require_user
from app.config import get_settings
from app.services.epayco import (
    CheckoutRequest, EpaycoConfirmation,
    get_epayco, map_epayco_state_to_db,
)
from app.services.supabase import admin
from app.services.precios import calcular_precios_pedido
from app.services.config_global import get_config
from app.services.whatsapp import enviar_mensaje_whatsapp  ← NUEVA

router = APIRouter(prefix="/api/pagos", tags=["pagos"])


# ─────────────────── INICIAR PAGO ───────────────────

class IniciarPagoRequest(BaseModel):
    transaccion_id: int = Field(..., description="ID de transaccion_b2b a pagar")


class IniciarPagoResponse(BaseModel):
    ok: bool
    pago_id: int
    referencia: str
    checkout_payload: dict
    monto_cop: int


def _obtener_desglose_pago(db, transaccion_id: int, monto_cop: int) -> tuple[float, float]:
    """Devuelve (monto_viverista, monto_plataforma) para un pago.

    Regla del Productor v2: viverista recibe el precio mayorista siempre.
    Fase 4 estrategia:
      1. Si la transaccion tiene comision_plataforma poblada por el motor
         nuevo → usar ese valor directo (fuente de verdad = motor matricial).
      2. Si no, recalcular al vuelo desde la cotización asociada usando
         calcular_precios_pedido() para no dejar el desglose incorrecto.
      3. Fallback conservador: monto_plataforma = 0, monto_viverista = monto_cop.
         Preferible dejar al viverista con todo que cobrarle una comisión mal
         calculada; la conciliación admin lo detecta y corrige.
    
    NOTA FASE 10.4: Este desglose se calcula DESPUÉS de aplicar el descuento B2B
    temporal en iniciar_pago(). Por eso el monto_cop que recibe aquí es el
    DESCUENTO YA APLICADO si es B2B.
    """
    # Estrategia 1 — Usar el desglose YA calculado por el motor
    try:
        txn = db.table("transacciones_b2b").select(
            "cliente_id, cotizacion_id, precio_total, comision_plataforma"
        ).eq("transaccion_id", transaccion_id).limit(1).execute()
        if txn.data:
            row = txn.data[0]
            comision = row.get("comision_plataforma")
            if comision is not None and float(comision) > 0:
                monto_plataforma = float(comision)
                monto_viverista = float(monto_cop) - monto_plataforma
                return (round(monto_viverista, 2), round(monto_plataforma, 2))
    except Exception:
        pass

    # Estrategia 2 — Recalcular desde la cotización
    try:
        cot_id = row.get("cotizacion_id") if txn.data else None
        cliente_id = row.get("cliente_id") if txn.data else None
        if cot_id and cliente_id:
            cot = db.table("cotizaciones").select(
                "items"
            ).eq("cotizacion_id", cot_id).limit(1).execute()
            cli = db.table("clientes").select(
                "cliente_id, es_guest, tipo_cliente, activo"
            ).eq("cliente_id", cliente_id).limit(1).execute()

            if cot.data and cli.data:
                items = cot.data[0].get("items") or []
                cliente = cli.data[0]
                resultado = calcular_precios_pedido(
                    cliente=cliente,
                    items=items,
                    plazo="inmediato",
                )
                totales = resultado["totales"]
                return (
                    round(totales["monto_viverista_total"], 2),
                    round(totales["monto_viveroonline_bruto_total"], 2),
                )
    except Exception:
        pass

    # Estrategia 3 — Fallback conservador (protege al viverista)
    return (float(monto_cop), 0.0)

# ─────────────────────────────────────────────────────────────────
# FASE 2: Funciones para Escenario 3 (split de pagos)
# ─────────────────────────────────────────────────────────────────

async def calcular_escenario_3_pago(
    db,
    pago_id: int,
    monto_plantas: float,
    monto_flete: float,
    es_b2b: bool,
    total_carrito: float,
    descuento_b2b_pct: float = 12.0
) -> dict:
    """Calcula los splits de pago usando función SQL calcular_escenario_3().
    
    Retorna dict con splits viverista/plataforma para plantas y flete.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Llamar función SQL
        resultado = db.rpc(
            "calcular_escenario_3",
            {
                "p_pago_id": pago_id,
                "p_plantas_monto": monto_plantas,
                "p_flete_monto": monto_flete,
                "p_es_b2b": es_b2b,
                "p_total_carrito": total_carrito,
                "p_descuento_b2b_pct": descuento_b2b_pct,
            }
        ).execute()
        
        if resultado.data and len(resultado.data) > 0:
            return resultado.data[0]
        else:
            logger.error(f"[FASE 2] RPC calcular_escenario_3 retornó datos vacíos")
            return None
            
    except Exception as e:
        logger.error(f"[FASE 2] Error llamando calcular_escenario_3: {e}")
        return None


async def crear_transferencia_viverista(
    db,
    vivero_id: int,
    pago_id: int,
    splits: dict
) -> int:
    """Crea registro en transferencias_viverista con splits del pago.
    
    Retorna transfer_id si éxito, None si falla.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        transfer_resp = db.table("transferencias_viverista").insert({
            "vivero_id": vivero_id,
            "pago_id": pago_id,
            "monto_plantas": splits.get("viverista_plantas"),
            "monto_flete": splits.get("viverista_flete"),
            "monto_total": splits.get("viverista_total"),
            "comision_plataforma_plantas": splits.get("plataforma_plantas"),
            "comision_plataforma_flete": splits.get("plataforma_flete"),
            "comision_descuento_b2b": splits.get("plataforma_descuento", 0),
            "comision_total_plataforma": splits.get("plataforma_total"),
            "descuento_b2b_pct": splits.get("descuento_b2b", 0),
            "estado": "pendiente_transferencia",  # Pendiente confirmación
            "notas": f"Escenario 3 - Pago {pago_id} confirmado",
        }).execute()
        
        if transfer_resp.data:
            transfer_id = transfer_resp.data[0].get("transfer_id")
            logger.info(f"[FASE 2] Transferencia creada: {transfer_id} para pago {pago_id}")
            return transfer_id
        else:
            logger.error(f"[FASE 2] No se pudo crear transferencia para pago {pago_id}")
            return None
            
    except Exception as e:
        logger.error(f"[FASE 2] Error creando transferencia: {e}")
        return None

# ───────────────────────────────────────────────────────────────────
@router.post("/iniciar", response_model=IniciarPagoResponse)
async def iniciar_pago(req: IniciarPagoRequest, user: UserContext = Depends(require_user)):
    """Inicia pago de una transacción B2B vía ePayco.
    
    Fase 10.4 (24 ago 2026):
    - Si cliente es B2B (no guest) Y fintech_activa=False en config
      → Aplica descuento temporal 12% al monto a cobrar
    - Cuando fintech esté confirmada, activa fintech_activa=true en Supabase
      → Descuento se desactiva automáticamente (sin cambiar código)
    """
    s = get_settings()
    epayco = get_epayco()
    if not epayco.is_configured:
        raise HTTPException(503, detail="Servicio de pagos no configurado")

    db = admin()

    txn_resp = db.table("transacciones_b2b").select(
        "transaccion_id, cliente_id, precio_total, estado, "
        "clientes(nombre_empresa, nombre_representante, whatsapp_numero, es_guest)"
    ).eq("transaccion_id", req.transaccion_id).limit(1).execute()
    if not txn_resp.data:
        raise HTTPException(404, detail="Transacción no encontrada")
    txn = txn_resp.data[0]

    if user.rol == "comprador" and txn["cliente_id"] != user.cliente_id:
        raise HTTPException(403, detail="No puedes pagar esta transacción")
    if user.rol not in ("comprador", "admin"):
        raise HTTPException(403, detail="Solo compradores pueden iniciar pagos")

    if txn["estado"] in ("pagada", "completada"):
        raise HTTPException(400, detail="Esta transacción ya fue pagada")

    monto_cop = int(float(txn["precio_total"]))
    cliente = txn.get("clientes") or {}

    # ═══════════════════════════════════════════════════════════════════════
    # 🔴 FASE 10.4: Descuento B2B temporal 12% (24 ago 2026)
    # ═══════════════════════════════════════════════════════════════════════
    # ANTES: monto_cop = precio_total (sin descuento)
    # AHORA: Si B2B + fintech_activa=False + monto >= 5 SMLMV
    #        → aplica 12% descuento
    # VALIDACIÓN: Descuentos solo para compras >= umbral SMLMV (igual a precios.py)
    # REVERTIR: Cuando fintech esté confirmada, activa fintech_activa=true
    #          en tabla configuracion_global y el descuento se desactiva automático
    # ═════════════════════════════════════════════════════════════════════════
    
    import logging
    logger = logging.getLogger(__name__)
    
    es_b2b = not cliente.get("es_guest", False)
    fintech_activa = bool(get_config("fintech_activa", default=False))
    
    # Leer umbral SMLMV de config (mismo que precios.py línea 124-125)
    smlmv = float(get_config("smlmv_actual", default=1_750_905))
    umbral_smlmv = int(get_config("umbral_descuento_b2b_smlmv", default=5))
    umbral_pesos = smlmv * umbral_smlmv
    
    monto_original = monto_cop
    descuento_aplicado = False
    
    if es_b2b and not fintech_activa:
        # VALIDACIÓN: Solo aplicar descuento si supera umbral SMLMV
        if monto_cop >= umbral_pesos:
            # Aplicar descuento 12% sobre el monto total
            descuento_pesos = int(monto_cop * 0.12)
            monto_cop = monto_cop - descuento_pesos
            descuento_aplicado = True
            
            logger.info(
                f"[Fase 10.4] Descuento B2B 12% aplicado: "
                f"Monto ${monto_original:,} >= Umbral ${umbral_pesos:,} (5 SMLMV × ${smlmv:,.0f}) "
                f"→ ${monto_original:,} → ${monto_cop:,} "
                f"(ahorro: ${descuento_pesos:,})"
            )
        else:
            # Compra por debajo del umbral — NO aplica descuento
            logger.info(
                f"[Fase 10.4] Descuento B2B NO aplicado: "
                f"Monto ${monto_original:,} < Umbral ${umbral_pesos:,} (5 SMLMV × ${smlmv:,.0f}) "
                f"Cliente B2B pero compra pequeña → Sin descuento"
            )
    elif es_b2b and fintech_activa:
        # Fintech activa — descuento será manejado por calcular_precios_pedido()
        logger.info(
            f"[Fase 10.4] Descuento B2B delegado a fintech: "
            f"fintech_activa=true → Usar descuentos de financiamiento"
        )
    else:
        # Cliente guest o no B2B — sin descuento
        logger.info(
            f"[Fase 10.4] Descuento B2B NO aplicado: "
            f"Cliente es guest={not es_b2b} → Sin descuento"
        )
    
    # ═════════════════════════════════════════════════════════════════════════

    response_url = f"{s.app_base_url}/pagos/resultado"
    confirmation_url = f"{s.app_base_url}/api/pagos/confirmacion"
    checkout_req = CheckoutRequest(
        transaccion_id=req.transaccion_id,
        monto_cop=monto_cop,
        descripcion=f"viveroonline.com.co · Transacción #{req.transaccion_id}",
        nombre_cliente=cliente.get("nombre_representante") or cliente.get("nombre_empresa") or "Cliente",
        telefono_cliente=cliente.get("whatsapp_numero"),
    )
    payload = epayco.build_checkout_payload(checkout_req, response_url, confirmation_url)
    referencia = payload["invoice"]

    # ── Fase 4: desglose desde el motor matricial (no más hardcoded 0.05) ──
    # NOTA: El monto_cop ya tiene descuento aplicado si es B2B sin fintech
    monto_viverista, monto_plataforma = _obtener_desglose_pago(db, req.transaccion_id, monto_cop)

    pago_resp = db.table("pagos").insert({
        "transaccion_id": req.transaccion_id,
        "monto_total": monto_cop,  # CON descuento B2B si aplica
        "moneda": "COP",
        "estado_pago": "pendiente",
        "metodo": "epayco",
        "referencia_externa": referencia,
        "monto_viverista": monto_viverista,
        "monto_plataforma": monto_plataforma,
    }).execute()
    if not pago_resp.data:
        raise HTTPException(500, detail="No se pudo registrar el pago")

    return IniciarPagoResponse(
        ok=True,
        pago_id=pago_resp.data[0]["pago_id"],
        referencia=referencia,
        checkout_payload=payload,
        monto_cop=monto_cop,
    )


# ─────────────────── WEBHOOK CONFIRMACIÓN ───────────────────

@router.post("/confirmacion")
async def confirmar_pago(
    request: Request,
    x_id_factura: str = Form(""),
    x_ref_payco: str = Form(""),
    x_amount: str = Form("0"),
    x_currency_code: str = Form("COP"),
    x_response: str = Form(""),
    x_response_reason_text: str = Form(""),
    x_transaction_id: str = Form(""),
    x_signature: str = Form(""),
    x_id_invoice: Optional[str] = Form(None),
):
    """Webhook de confirmación de ePayco.
    
    Recibe la notificación de pago aprobado/rechazado/pendiente
    y actualiza el estado en Supabase.
    """
    epayco = get_epayco()
    db = admin()

    conf = EpaycoConfirmation(
        x_id_factura=x_id_factura,
        x_id_invoice=x_id_invoice,
        x_ref_payco=x_ref_payco,
        x_amount=x_amount,
        x_currency_code=x_currency_code,
        x_response=x_response,
        x_response_reason_text=x_response_reason_text,
        x_transaction_id=x_transaction_id,
        x_signature=x_signature,
    )

    if not epayco.validate_signature(conf):
        try:
            db.table("log_ia").insert({
                "tipo_operacion": "epayco_invalid_signature",
                "input_data": conf.model_dump(),
            }).execute()
        except Exception:
            pass
        return {"ok": False, "reason": "invalid_signature"}

    pago_resp = db.table("pagos").select(
        "pago_id, transaccion_id, estado_pago, tipo, suscripcion_id"
    ).eq("referencia_externa", x_id_factura).limit(1).execute()
    if not pago_resp.data:
        return {"ok": False, "reason": "pago_no_encontrado"}
    pago = pago_resp.data[0]

    new_state = map_epayco_state_to_db(x_response)
    db.table("pagos").update({
        "estado_pago": new_state,
        "epayco_id": x_ref_payco,
        "fecha_confirmacion": "now()",
        "webhook_payload": conf.model_dump(),
    }).eq("pago_id", pago["pago_id"]).execute()

    # ── POST-PAGO: activar según tipo ──────────────────
    if new_state == "aprobado":
        tipo_pago = pago.get("tipo") or "transaccion_b2b"

        if tipo_pago == "suscripcion" and pago.get("suscripcion_id"):
            # Activar Plan Inteligencia por 30 días
            from datetime import datetime, timedelta, timezone
            ahora = datetime.now(timezone.utc)
            proximo_cobro = ahora + timedelta(days=30)
            db.table("suscripciones").update({
                "estado": "activa",
                "fecha_inicio": ahora.isoformat(),
                "fecha_proximo_cobro": proximo_cobro.isoformat(),
                "epayco_subscription_id": x_ref_payco,
            }).eq("suscripcion_id", pago["suscripcion_id"]).execute()

             elif pago.get("transaccion_id"):
            # ═══════════════════════════════════════════════════════════════
            # FASE 2: ESCENARIO 3 - Split automático de pagos
            # ═══════════════════════════════════════════════════════════════
            import logging
            logger = logging.getLogger(__name__)
            
            try:
                # Obtener datos de la cotización para calcular splits
                txn = db.table("transacciones_b2b").select(
                    "transaccion_id, vivero_id, cliente_id, cotizacion_id, "
                    "precio_total, estado"
                ).eq("transaccion_id", pago["transaccion_id"]).limit(1).execute()
                
                if txn.data:
                    txn_row = txn.data[0]
                    vivero_id = txn_row.get("vivero_id")
                    cotizacion_id = txn_row.get("cotizacion_id")
                    cliente_id = txn_row.get("cliente_id")
                    
                    # Obtener monto total y desglose plantas/flete
                    cot = db.table("cotizaciones").select(
                        "monto_plantas, monto_flete, tipo_cliente, es_b2b"
                    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
                    
                    if cot.data:
                        cot_row = cot.data[0]
                        monto_plantas = float(cot_row.get("monto_plantas", 0))
                        monto_flete = float(cot_row.get("monto_flete", 0))
                        es_b2b = bool(cot_row.get("es_b2b", False))
                        total_carrito = monto_plantas + monto_flete
                        
                        # Leer descuento B2B desde config
                        descuento_b2b_pct = 12.0  # Default Escenario 3
                        
                        # LLAMAR calcular_escenario_3()
                        splits = await calcular_escenario_3_pago(
                            db=db,
                            pago_id=pago["pago_id"],
                            monto_plantas=monto_plantas,
                            monto_flete=monto_flete,
                            es_b2b=es_b2b,
                            total_carrito=total_carrito,
                            descuento_b2b_pct=descuento_b2b_pct
                        )
                        
                        if splits:
                            # CREAR transferencia_viverista
                            transfer_id = await crear_transferencia_viverista(
                                db=db,
                                vivero_id=vivero_id,
                                pago_id=pago["pago_id"],
                                splits=splits
                            )
                            
                            if transfer_id:
                                logger.info(
                                    f"[FASE 2] Escenario 3 OK: "
                                    f"Pago {pago['pago_id']} → Transfer {transfer_id}"
                                )
                                
                                # ENVIAR WhatsApp a viverista con desglose
                                try:
                                    whatsapp_msg = (
                                        f"✅ *PAGO CONFIRMADO*\n\n"
                                        f"Pago #{pago['pago_id']}\n"
                                        f"Transacción: {pago['transaccion_id']}\n\n"
                                        f"*DESGLOSE:*\n"
                                        f"Plantas: ${splits.get('viverista_plantas', 0):,.0f}\n"
                                        f"Flete: ${splits.get('viverista_flete', 0):,.0f}\n"
                                        f"*TOTAL: ${splits.get('viverista_total', 0):,.0f}*\n\n"
                                        f"Estado: Pendiente transferencia"
                                    )
                                    
                                    # Obtener WhatsApp del viverista
                                    vivero_resp = db.table("viveros").select(
                                        "whatsapp_numero"
                                    ).eq("vivero_id", vivero_id).limit(1).execute()
                                    
                                    if vivero_resp.data:
                                        numero = vivero_resp.data[0].get("whatsapp_numero")
                                        if numero:
                                            await enviar_mensaje_whatsapp(
                                                numero_whatsapp=numero,
                                                mensaje=whatsapp_msg
                                            )
                                            logger.info(
                                                f"[FASE 2] WhatsApp enviado a vivero {vivero_id}"
                                            )
                                except Exception as e:
                                    logger.warning(f"[FASE 2] Error enviando WhatsApp: {e}")
                            else:
                                logger.error(
                                    f"[FASE 2] No se pudo crear transferencia para pago {pago['pago_id']}"
                                )
                        else:
                            logger.error(
                                f"[FASE 2] calcular_escenario_3 retornó None para pago {pago['pago_id']}"
                            )
            except Exception as e:
                # No bloqueamos el flujo de pago si Escenario 3 falla
                logger.exception(f"[FASE 2] Error en Escenario 3: {e}")
            
            # ═══════════════════════════════════════════════════════════════
            
            # Pago B2B del marketplace — marcar como pagada
            db.table("transacciones_b2b").update({
                "estado": "pagada",
            }).eq("transaccion_id", pago["transaccion_id"]).execute()

            # ── LOGÍSTICA: generar entregas y notificar viveristas ──

            # ── LOGÍSTICA: generar entregas y notificar viveristas ──
            try:
                txn = db.table("transacciones_b2b").select(
                    "cotizacion_id"
                ).eq("transaccion_id", pago["transaccion_id"]).limit(1).execute()

                if txn.data and txn.data[0].get("cotizacion_id"):
                    cotizacion_id = txn.data[0]["cotizacion_id"]
                    from app.services.logistica import generar_entregas_y_notificar
                    await generar_entregas_y_notificar(cotizacion_id)
            except Exception as e:
                # No bloqueamos el flujo de pago si la logística falla
                import logging
                logging.getLogger(__name__).exception(f"Error generando entregas post-pago: {e}")

    return {"ok": True, "estado": new_state}


# ─────────────────── ESTADO DEL PAGO ───────────────────

@router.get("/estado/{pago_id}")
async def estado_pago(pago_id: int, user: UserContext = Depends(require_user)):
    """Retorna el estado actual de un pago."""
    db = admin()
    resp = db.table("pagos").select(
        "pago_id, transaccion_id, monto_total, estado_pago, "
        "metodo, referencia_externa, fecha_creacion, fecha_confirmacion"
    ).eq("pago_id", pago_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(404, detail="Pago no encontrado")
    return {"ok": True, "pago": resp.data[0]}
