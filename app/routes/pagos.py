"""Rutas de pagos vía ePayco - ESCENARIO 3 FASE 2 CORREGIDO.

CORRECCIONES APLICADAS (Sep 5, 2026):
- PROBLEMA 1: db.rpc().execute() no está siendo awaited (async/await)
- PROBLEMA 2: Parámetros RPC podrían estar mal formados
- PROBLEMA 3: Falta validación de respuesta

SOLUCIONES:
✅ Agregar await a db.rpc().execute()
✅ Validar tipos de parámetros antes de enviar
✅ Mejor manejo de errores con logging detallado
"""

from __future__ import annotations
import logging
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pagos", tags=["pagos"])


# ─────────────────── ESCENARIO 3 FASE 2 - CORE FUNCTIONS ───────────────────

async def calcular_escenario_3_pago(
    db,
    pago_id: int,
    monto_plantas: float,
    monto_flete: float,
    es_b2b: bool,
    total_carrito: float,
    descuento_b2b_pct: float = 12.0
) -> dict | None:
    """Calcula los splits de pago usando función SQL calcular_escenario_3().
    
    CORRECCIONES FASE 2:
    - Convierte parámetros a tipos correctos antes de enviar
    - Usa await en db.rpc().execute()
    - Mejor logging de errores
    
    Args:
        db: Supabase client
        pago_id: ID del pago
        monto_plantas: Total de plantas
        monto_flete: Total de flete
        es_b2b: Si es B2B (boolean)
        total_carrito: Total del carrito
        descuento_b2b_pct: Porcentaje descuento B2B
    
    Returns:
        dict con splits {viverista_plantas, viverista_flete, viverista_total, 
                         plataforma_plantas, plataforma_flete, plataforma_total, ...}
        None si hay error
    """
    try:
        # CONVERSIÓN DE TIPOS - Validar antes de RPC
        pago_id_int = int(pago_id)
        monto_plantas_float = float(monto_plantas)
        monto_flete_float = float(monto_flete)
        es_b2b_bool = bool(es_b2b)
        total_carrito_float = float(total_carrito)
        descuento_b2b_pct_float = float(descuento_b2b_pct)
        
        logger.info(
            f"[ESCENARIO 3] Invocando RPC con: "
            f"pago_id={pago_id_int}, "
            f"plantas={monto_plantas_float}, "
            f"flete={monto_flete_float}, "
            f"es_b2b={es_b2b_bool}, "
            f"total={total_carrito_float}, "
            f"descto={descuento_b2b_pct_float}%"
        )
        
        # LLAMADA RPC CON AWAIT - CORREGIDO
        resultado = await db.rpc(
            "calcular_escenario_3",
            {
                "p_pago_id": pago_id_int,
                "p_plantas_monto": monto_plantas_float,
                "p_flete_monto": monto_flete_float,
                "p_es_b2b": es_b2b_bool,
                "p_total_carrito": total_carrito_float,
                "p_descuento_b2b_pct": descuento_b2b_pct_float,
            }
        ).execute()
        
        # VALIDAR RESPUESTA
        if not resultado or not resultado.data:
            logger.error(f"[ESCENARIO 3] RPC retornó data vacía: {resultado}")
            return None
        
        if len(resultado.data) == 0:
            logger.error(f"[ESCENARIO 3] RPC retornó array vacío")
            return None
        
        splits = resultado.data[0]
        logger.info(
            f"[ESCENARIO 3] RPC SUCCESS - Viverista: ${splits.get('viverista_total')}, "
            f"Plataforma: ${splits.get('plataforma_total')}"
        )
        
        return splits
            
    except Exception as e:
        logger.error(
            f"[ESCENARIO 3] ERROR en calcular_escenario_3_pago: {type(e).__name__}: {str(e)}",
            exc_info=True
        )
        return None


async def crear_transferencia_viverista(
    db,
    vivero_id: int,
    pago_id: int,
    splits: dict
) -> int | None:
    """Crea registro en transferencias_viverista con splits calculados.
    
    Args:
        db: Supabase client
        vivero_id: ID del vivero/viverista
        pago_id: ID del pago
        splits: dict con splits calculados
    
    Returns:
        transferencia_id si OK, None si error
    """
    try:
        # PREPARAR DATOS
        transfer_data = {
            "vivero_id": vivero_id,
            "pago_id": pago_id,
            "monto_plantas": float(splits.get("viverista_plantas", 0)),
            "monto_flete": float(splits.get("viverista_flete", 0)),
            "viverista_total": float(splits.get("viverista_total", 0)),
            "plataforma_total": float(splits.get("plataforma_total", 0)),
            "estado": "pendiente",
        }
        
        logger.info(
            f"[ESCENARIO 3] Creando transferencia: "
            f"vivero={vivero_id}, pago={pago_id}, "
            f"viverista=${transfer_data['viverista_total']}"
        )
        
        # INSERT
        result = await db.table("transferencias_viverista").insert(
            transfer_data
        ).execute()
        
        if result.data and len(result.data) > 0:
            transfer_id = result.data[0].get("transferencia_id")
            logger.info(f"[ESCENARIO 3] Transferencia creada: ID={transfer_id}")
            return transfer_id
        else:
            logger.error(f"[ESCENARIO 3] INSERT no retornó data")
            return None
            
    except Exception as e:
        logger.error(
            f"[ESCENARIO 3] ERROR creando transferencia: {type(e).__name__}: {str(e)}",
            exc_info=True
        )
        return None


async def enviar_notificacion_viverista(
    db,
    vivero_id: int,
    pago_id: int,
    splits: dict
) -> bool:
    """Envía WhatsApp al viverista con desglose de pago.
    
    Args:
        db: Supabase client
        vivero_id: ID del vivero
        pago_id: ID del pago
        splits: dict con splits
    
    Returns:
        True si OK, False si error
    """
    try:
        # OBTENER INFO VIVERISTA
        vivero = await db.table("viveros").select(
            "nombre_vivero, numero_whatsapp"
        ).eq("vivero_id", vivero_id).limit(1).execute()
        
        if not vivero.data:
            logger.warning(f"[ESCENARIO 3] No encontré vivero {vivero_id}")
            return False
        
        viv_row = vivero.data[0]
        nombre = viv_row.get("nombre_vivero", "Viverista")
        whatsapp = viv_row.get("numero_whatsapp")
        
        if not whatsapp:
            logger.warning(f"[ESCENARIO 3] Vivero {vivero_id} sin WhatsApp")
            return False
        
        # CONSTRUIR MENSAJE
        mensaje = (
            f"Hola {nombre},\n\n"
            f"Nueva venta confirmada (Pago #{pago_id}):\n\n"
            f"💚 Te corresponde:\n"
            f"  Plantas: ${splits.get('viverista_plantas', 0):,.0f}\n"
            f"  Flete: ${splits.get('viverista_flete', 0):,.0f}\n"
            f"  Total: ${splits.get('viverista_total', 0):,.0f}\n\n"
            f"El dinero se transferirá después de validar el comprobante de pago."
        )
        
        logger.info(f"[ESCENARIO 3] Enviando WhatsApp a {whatsapp}")
        # TODO: Implementar envío real via whatsapp_meta.py
        # await send_whatsapp_message(whatsapp, mensaje)
        
        return True
        
    except Exception as e:
        logger.error(
            f"[ESCENARIO 3] ERROR notificando viverista: {type(e).__name__}: {str(e)}",
            exc_info=True
        )
        return False


# ─────────────────── INICIAR PAGO - INTEGRATE ESCENARIO 3 ───────────────────

class IniciarPagoRequest(BaseModel):
    transaccion_id: int = Field(..., description="ID de transaccion_b2b a pagar")


class IniciarPagoResponse(BaseModel):
    ok: bool
    pago_id: int
    referencia: str
    checkout_payload: dict
    monto_cop: int


@router.post("/iniciar", response_model=IniciarPagoResponse)
async def iniciar_pago(
    body: IniciarPagoRequest,
    user: UserContext = Depends(require_user),
):
    """Inicia pago con ePayco.
    
    ESCENARIO 3:
    - Calcula splits plantas/flete
    - Crea registro en transferencias_viverista
    - Notifica viverista
    - Procede con checkout ePayco
    """
    db = admin()
    
    try:
        # OBTENER TRANSACCIÓN
        txn = await db.table("transacciones_b2b").select(
            "*"
        ).eq("transaccion_id", body.transaccion_id).limit(1).execute()
        
        if not txn.data:
            raise HTTPException(status_code=404, detail="Transacción no encontrada")
        
        txn_row = txn.data[0]
        
        # OBTENER COTIZACIÓN (contiene monto_plantas, monto_flete)
        cot = await db.table("cotizaciones").select(
            "monto_plantas, monto_flete, es_b2b"
        ).eq("cotizacion_id", txn_row["cotizacion_id"]).limit(1).execute()
        
        if not cot.data:
            logger.error(f"[ESCENARIO 3] No encontré cotización {txn_row['cotizacion_id']}")
            raise HTTPException(status_code=404, detail="Cotización no encontrada")
        
        cot_row = cot.data[0]
        monto_plantas = float(cot_row.get("monto_plantas", 0))
        monto_flete = float(cot_row.get("monto_flete", 0))
        es_b2b = bool(cot_row.get("es_b2b", False))
        total_carrito = monto_plantas + monto_flete
        
        logger.info(
            f"[ESCENARIO 3] Iniciando pago para transacción {body.transaccion_id}: "
            f"plantas=${monto_plantas}, flete=${monto_flete}, es_b2b={es_b2b}"
        )
        
        # CREAR PAGO (estado: pendiente)
        pago_data = {
            "transaccion_id": body.transaccion_id,
            "estado_pago": "pendiente",
            "monto_total": total_carrito,
        }
        
        pago = await db.table("pagos").insert(pago_data).execute()
        
        if not pago.data or len(pago.data) == 0:
            raise HTTPException(status_code=500, detail="Error creando pago")
        
        pago_id = pago.data[0]["pago_id"]
        logger.info(f"[ESCENARIO 3] Pago creado: ID={pago_id}")
        
        # ✅ ESCENARIO 3 - CALCULAR SPLITS
        splits = await calcular_escenario_3_pago(
            db=db,
            pago_id=pago_id,
            monto_plantas=monto_plantas,
            monto_flete=monto_flete,
            es_b2b=es_b2b,
            total_carrito=total_carrito,
            descuento_b2b_pct=12.0  # Default
        )
        
        if splits:
            # CREAR TRANSFERENCIA
            vivero_id = txn_row.get("vivero_id")
            transfer_id = await crear_transferencia_viverista(
                db=db,
                vivero_id=vivero_id,
                pago_id=pago_id,
                splits=splits
            )
            
            if transfer_id:
                # NOTIFICAR VIVERISTA
                await enviar_notificacion_viverista(
                    db=db,
                    vivero_id=vivero_id,
                    pago_id=pago_id,
                    splits=splits
                )
        else:
            logger.warning(f"[ESCENARIO 3] No se calcularon splits para pago {pago_id}")
        
        # GENERAR CHECKOUT EPAYCO
        epayco = get_epayco()
        checkout_req = CheckoutRequest(
            monto_centavos=int(total_carrito * 100),
            referencia=f"PAG-{pago_id}",
        )
        checkout_payload = epayco.build_checkout(checkout_req)
        
        return IniciarPagoResponse(
            ok=True,
            pago_id=pago_id,
            referencia=checkout_payload.get("ref_payco", ""),
            checkout_payload=checkout_payload,
            monto_cop=int(total_carrito),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[ESCENARIO 3] ERROR en iniciar_pago: {type(e).__name__}: {str(e)}",
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Error iniciando pago: {str(e)}")
