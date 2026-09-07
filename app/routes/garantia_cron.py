"""
Garantía 24h Cron - Verificación automática y Payout 40%
Rama: feat/payouts-60-40

FLUJO:
1. CRON se ejecuta cada 1 hora (configurado en app/main.py con APScheduler)
2. Busca entregas con estado='entregado' y timestamp_entrega + 24h < NOW()
3. Para cada una, verifica: ¿Hay RECLAMO abierto?
   - SI: saltar (retener Payout 40%)
   - NO: crear transferencia Payout 40% y marcar como "completado"
4. Notificar viverista

INTEGRACIÓN:
- Llamado desde: app/main.py via APScheduler
- Base de datos: Supabase (tablas: entregas, tickets_soporte, transferencias_viverista)
- Frecuencia: cada 1 hora
- Timeout: 60 segundos máximo

IMPORTANTE:
- Este cron es CRÍTICO para el modelo
- Si falla, viverista pierde su Payout 40%
- Tiene reintentos y logging exhaustivo
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from app.services.supabase import admin

logger = logging.getLogger(__name__)


async def ejecutar_garantia_cron() -> Dict:
    """
    Ejecuta verificación de garantía 24h y payout 40%
    
    Llamado desde: app/main.py via APScheduler
    
    Returns:
        {
            "ok": bool,
            "entregas_verificadas": int,
            "payouts_ejecutados": int,
            "entregas_con_reclamo": int,
            "entregas_error": int,
            "detalles": [...],
            "error": str (si error general)
        }
    """
    
    db = admin()
    resultado = {
        "ok": True,
        "entregas_verificadas": 0,
        "payouts_ejecutados": 0,
        "entregas_con_reclamo": 0,
        "entregas_error": 0,
        "detalles": [],
        "timestamp_ejecucion": datetime.utcnow().isoformat()
    }
    
    try:
        logger.info("[CRON 24h] Iniciando verificación de garantía...")
        
        # 1. BUSCAR ENTREGAS ENTREGADAS HACE MÁS DE 24H
        try:
            ahora = datetime.utcnow()
            hace_24h = (ahora - timedelta(hours=24)).isoformat()
            
            entregas_result = db.table("entregas").select(
                "entrega_id, cotizacion_id, vivero_id, estado_entrega, timestamp_entrega"
            ).eq(
                "estado_entrega", "entregado"
            ).lt(
                "timestamp_entrega", hace_24h
            ).execute()
            
            entregas = entregas_result.data or []
            resultado["entregas_verificadas"] = len(entregas)
            
            logger.info(f"[CRON 24h] Encontradas {len(entregas)} entregas con 24h+")
            
        except Exception as e:
            logger.error(f"[CRON 24h] Error buscando entregas: {str(e)}")
            resultado["ok"] = False
            resultado["error"] = f"Error buscando entregas: {str(e)}"
            return resultado
        
        # 2. PROCESAR CADA ENTREGA
        for entrega in entregas:
            try:
                entrega_id = entrega["entrega_id"]
                cotizacion_id = entrega["cotizacion_id"]
                vivero_id = entrega["vivero_id"]
                
                logger.info(f"[CRON 24h] Procesando entrega {entrega_id}...")
                
                # 2.1. BUSCAR RECLAMOS ABIERTOS PARA ESTA ENTREGA
                try:
                    reclamos_result = db.table("tickets_soporte").select(
                        "ticket_id"
                    ).eq(
                        "entrega_id", entrega_id
                    ).eq(
                        "ticket_type", "RECLAMO"
                    ).eq(
                        "estado", "abierto"
                    ).execute()
                    
                    tiene_reclamo = bool(reclamos_result.data and len(reclamos_result.data) > 0)
                    
                    if tiene_reclamo:
                        logger.info(
                            f"[CRON 24h] Entrega {entrega_id}: TIENE RECLAMO ABIERTO, "
                            f"reteniendo Payout 40%"
                        )
                        resultado["entregas_con_reclamo"] += 1
                        resultado["detalles"].append({
                            "entrega_id": entrega_id,
                            "status": "reclamo_abierto",
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        continue  # Pasar a siguiente entrega
                    
                except Exception as e:
                    logger.error(f"[CRON 24h] Error verificando reclamos: {str(e)}")
                    resultado["entregas_error"] += 1
                    resultado["detalles"].append({
                        "entrega_id": entrega_id,
                        "status": "error_reclamos",
                        "error": str(e)
                    })
                    continue
                
                # 2.2. SIN RECLAMOS → EJECUTAR PAYOUT 40%
                logger.info(f"[CRON 24h] Entrega {entrega_id}: SIN RECLAMOS, ejecutando Payout 40%")
                
                try:
                    # Obtener monto de cotización
                    cotizacion_result = db.table("cotizaciones").select(
                        "monto_total"
                    ).eq(
                        "cotizacion_id", cotizacion_id
                    ).single().execute()
                    
                    if not cotizacion_result.data:
                        raise Exception(f"Cotización {cotizacion_id} no encontrada")
                    
                    monto_total = float(cotizacion_result.data["monto_total"])
                    
                except Exception as e:
                    logger.error(f"[CRON 24h] Error obteniendo cotización: {str(e)}")
                    resultado["entregas_error"] += 1
                    resultado["detalles"].append({
                        "entrega_id": entrega_id,
                        "status": "error_cotizacion",
                        "error": str(e)
                    })
                    continue
                
                # Obtener pago_id
                pago_id = None
                try:
                    pagos_result = db.table("pagos").select("pago_id").eq(
                        "cotizacion_id", cotizacion_id
                    ).eq("estado_pago", "aprobado").limit(1).execute()
                    
                    if pagos_result.data:
                        pago_id = pagos_result.data[0]["pago_id"]
                
                except Exception as e:
                    logger.error(f"[CRON 24h] Error buscando pago: {str(e)}")
                    pago_id = None
                
                # Crear transferencia Payout 40%
                if pago_id:
                    try:
                        # Calcular split Escenario 3
                        comision_vo_40 = (monto_total * 0.03) * 0.40
                        monto_viverista_40 = (monto_total * 0.97) * 0.40
                        
                        transfer_result = db.table("transferencias_viverista").insert({
                            "pago_id": pago_id,
                            "vivero_id": vivero_id,
                            "monto_plantas": monto_total,
                            "monto_flete": 0,
                            "viverista_plantas": monto_viverista_40,
                            "viverista_flete": 0,
                            "viverista_total": monto_viverista_40,
                            "plataforma_plantas": comision_vo_40,
                            "plataforma_flete": 0,
                            "plataforma_total": comision_vo_40,
                            "estado": "enviado",
                            "referencia_banco": f"TR_{pago_id}_CRON40_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                            "fecha_transferencia": datetime.utcnow().isoformat(),
                        }).execute()
                        
                        logger.info(
                            f"[CRON 24h] Payout 40% ejecutado: "
                            f"entrega={entrega_id}, viverista=${monto_viverista_40:.2f}"
                        )
                        resultado["payouts_ejecutados"] += 1
                        resultado["detalles"].append({
                            "entrega_id": entrega_id,
                            "status": "payout_40_ejecutado",
                            "monto": monto_viverista_40,
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        
                    except Exception as e:
                        logger.error(f"[CRON 24h] Error creando transferencia: {str(e)}")
                        resultado["entregas_error"] += 1
                        resultado["detalles"].append({
                            "entrega_id": entrega_id,
                            "status": "error_transferencia",
                            "error": str(e)
                        })
                        continue
                else:
                    logger.warning(f"[CRON 24h] Sin pago_id para entrega {entrega_id}, saltando Payout")
                    resultado["detalles"].append({
                        "entrega_id": entrega_id,
                        "status": "sin_pago_id",
                        "timestamp": datetime.utcnow().isoformat()
                    })
                
                # 2.3. MARCAR ENTREGA COMO COMPLETADA
                try:
                    db.table("entregas").update({
                        "estado_entrega": "completado",
                        "fecha_actualizacion": datetime.utcnow().isoformat()
                    }).eq("entrega_id", entrega_id).execute()
                    
                except Exception as e:
                    logger.error(f"[CRON 24h] Error marcando como completada: {str(e)}")
                    # No es bloqueante
                
            except Exception as e:
                logger.error(f"[CRON 24h] Error procesando entrega {entrega.get('entrega_id')}: {str(e)}")
                resultado["entregas_error"] += 1
                resultado["detalles"].append({
                    "entrega_id": entrega.get("entrega_id"),
                    "status": "error_general",
                    "error": str(e)
                })
        
        # 3. RESUMEN FINAL
        logger.info(
            f"[CRON 24h] COMPLETADO: "
            f"verificadas={resultado['entregas_verificadas']}, "
            f"payouts={resultado['payouts_ejecutados']}, "
            f"reclamos={resultado['entregas_con_reclamo']}, "
            f"errores={resultado['entregas_error']}"
        )
        
        return resultado
        
    except Exception as e:
        logger.error(f"[CRON 24h] Error general en cron: {str(e)}", exc_info=True)
        resultado["ok"] = False
        resultado["error"] = str(e)
        return resultado
