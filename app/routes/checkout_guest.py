"""Checkout guest B2C — Fase 10.2 + 10.3 + FASE 10.4 (Descuento B2B)

Endpoints públicos SIN autenticación para el flujo de compra guest.

Fase 10.2 (26 jul): validate-cart + calcular-flete
Fase 10.3 (29 jul): create-order — crea cliente_guest + cotización + payment intent ePayco
Fase 10.4 (24 ago): Descuento B2B 12% sobre SUBTOTAL (no flete) con validación SMLMV
FASE 10.4 FIX (24 ago): Detectar cliente registrado B2B (no guest) si ya existe por email
FASE 10.4 SHOW (25 ago): Devolver descuento en respuesta para mostrar al cliente ANTES de pagar

Reglas B2C guest:
- Solo tier S+M
- Máx 10 unidades por producto
- Máx 120 plantas total
- Precios via motor matricial por categoría
- Reserva stock 15 min al iniciar pago
- Post-pago: reusa webhook /api/pagos/confirmacion existente
- NUEVO: Descuento 12% B2B si cliente registrado + compra >= 5 SMLMV + fintech_activa=false
- FIX: Si email pertenece a cliente registrado (es_guest=false), usar ese cliente (no crear guest)
- SHOW: Devolver descuento_pesos, descuento_porcentaje en respuesta para mostrar al cliente
"""
from __future__ import annotations
from typing import Optional
from datetime import datetime, timedelta, timezone
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, EmailStr

from app.config import get_settings
from app.services.epayco import CheckoutRequest, get_epayco
from app.services.supabase import admin
from app.services.config_global import get_matriz_comercial, get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public/checkout-guest", tags=["checkout-guest"])


# ═══════════════════════════════════════════════════════════
# Schemas
# ═══════════════════════════════════════════════════════════

class CartItem(BaseModel):
    inventario_id: int
    cantidad: int = Field(ge=1, le=10, description="Máximo 10 por producto")


class ValidateCartRequest(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=60)


class CalcularFleteRequest(BaseModel):
    ciudad: str = Field(min_length=2, max_length=100)
    items: list[CartItem] = Field(min_length=1)


class CreateOrderRequest(BaseModel):
    # Items
    items: list[CartItem] = Field(min_length=1, max_length=60)
    # Datos comprador (para factura)
    nombre: str = Field(min_length=3, max_length=150)
    tipo_documento: str = Field(pattern="^(CC|NIT|CE|PP)$")
    num_documento: str = Field(min_length=5, max_length=20)
    email: EmailStr
    whatsapp: str = Field(pattern="^3\\d{9}$", description="10 dígitos Colombia empezando con 3")
    # Datos entrega
    ciudad: str = Field(min_length=2, max_length=100)
    direccion: str = Field(min_length=10, max_length=300)
    referencia: Optional[str] = Field(default=None, max_length=200)
    mismo_receptor: bool = True
    receptor_nombre: Optional[str] = Field(default=None, max_length=150)
    receptor_telefono: Optional[str] = Field(default=None, max_length=20)
    fecha_entrega: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD")
    notas: Optional[str] = Field(default=None, max_length=500)
    # Aceptaciones legales
    acepta_perecedero: bool
    acepta_habeas_data: bool


class CreateOrderResponse(BaseModel):
    ok: bool
    cotizacion_id: int
    transaccion_id: int
    pago_id: int
    referencia: str
    monto_total: int
    # ═══ NUEVO (Fase 10.4 SHOW): Descuento para mostrar al cliente ═══
    descuento_pesos: int
    descuento_porcentaje: float
    monto_con_descuento: int
    # ═══════════════════════════════════════════════════════════════
    checkout_payload: dict
    expires_at: str  # ISO string


# ═══════════════════════════════════════════════════════════
# Helpers de flete y categorías
# ═══════════════════════════════════════════════════════════

_TIER_ORDER = {"S": 1, "M": 2, "L": 3, "XL": 4}

_ZONA_1 = {"Chía", "Cajicá", "Cota", "Tenjo", "Tabio", "Sopó", "Bogotá Norte"}
_ZONA_2 = {"Zipaquirá", "La Calera", "Facatativá", "Madrid", "Mosquera",
           "Funza", "Tocancipá", "Bogotá Centro", "Bogotá Sur", "Otro"}

_FLETE_STUB = {
    ("S", 1): 25_000, ("S", 2): 40_000,
    ("M", 1): 35_000, ("M", 2): 55_000,
    ("L", 1): 65_000, ("L", 2): 95_000,
    ("XL", 1): 95_000, ("XL", 2): 140_000,
}


def _resolver_zona(ciudad: str) -> int:
    if ciudad in _ZONA_1:
        return 1
    return 2


def _tier_mayor(tiers: list[str]) -> str:
    tiers_validos = [t for t in tiers if t in _TIER_ORDER]
    if not tiers_validos:
        return "M"
    return max(tiers_validos, key=lambda t: _TIER_ORDER[t])


def _calcular_flete_por_tarifa(db, ciudad: str, tier: str) -> int:
    try:
        resp = db.table("tarifas_logistica").select(
            "tarifa_cop"
        ).eq("ciudad", ciudad).eq("tier", tier).limit(1).execute()
        if resp.data and resp.data[0].get("tarifa_cop"):
            return int(resp.data[0]["tarifa_cop"])
    except Exception:
        pass
    zona = _resolver_zona(ciudad)
    return _FLETE_STUB.get((tier, zona), 55_000)


def _resolver_categorias_batch(db, inventario_ids: list[int]) -> dict[int, str]:
    if not inventario_ids:
        return {}
    resp = db.table("inventario").select(
        "inventario_id, categoria_producto, plantas(categoria_producto)"
    ).in_("inventario_id", inventario_ids).execute()
    resultado = {}
    for row in resp.data or []:
        override = row.get("categoria_producto")
        default = (row.get("plantas") or {}).get("categoria_producto")
        resultado[row["inventario_id"]] = override or default or "plantas_ornamentales"
    return resultado


def _validar_y_calcular_carrito(db, items_req: list[CartItem]) -> dict:
    """Valida items + calcula precio_comprador y precio_mayorista.

    Retorna dict con items validados, totales, errores.
    """
    total_unidades = sum(it.cantidad for it in items_req)
    if total_unidades > 120:
        return {
            "ok": False,
            "error_code": "limite_total",
            "mensaje": f"Máximo 120 plantas por compra (tenés {total_unidades})",
        }

    inv_ids = [it.inventario_id for it in items_req]
    inv_resp = db.table("inventario").select(
        "inventario_id, precio_mayorista, stock, estado_planta, "
        "logistics_tier, foto_ia_url, categoria_producto, "
        "plantas(nombre_comun, categoria_producto), "
        "viveros(nombre_vivero, ciudad)"
    ).in_("inventario_id", inv_ids).execute()
    inv_map = {r["inventario_id"]: r for r in (inv_resp.data or [])}

    matriz = get_matriz_comercial()
    markups_b2c = matriz.get("markup_b2c", {})

    items_validados = []
    errores = []
    total_plantas = 0
    total_comprador = 0
    total_mayorista = 0
    tiers_presentes = []

    for req_item in items_req:
        inv = inv_map.get(req_item.inventario_id)

        if not inv:
            errores.append({"inventario_id": req_item.inventario_id, "error": "no_existe"})
            continue

        tier = inv.get("logistics_tier", "M")
        if tier not in ("S", "M"):
            errores.append({"inventario_id": req_item.inventario_id, "error": "tier_no_permitido"})
            continue

        if inv.get("estado_planta") != "disponible":
            errores.append({"inventario_id": req_item.inventario_id, "error": "no_disponible"})
            continue

        stock = inv.get("stock") or 0
        if stock < req_item.cantidad:
            errores.append({
                "inventario_id": req_item.inventario_id,
                "error": "stock_insuficiente",
                "stock_disponible": stock,
            })
            continue

        override_inv = inv.get("categoria_producto")
        default_planta = (inv.get("plantas") or {}).get("categoria_producto")
        categoria = override_inv or default_planta or "plantas_ornamentales"
        markup = float(markups_b2c.get(categoria, 0.20))
        precio_mayorista = float(inv.get("precio_mayorista") or 0)
        precio_comprador = round(precio_mayorista * (1 + markup))
        subtotal_comprador = precio_comprador * req_item.cantidad
        subtotal_mayorista = round(precio_mayorista) * req_item.cantidad

        planta = inv.get("plantas") or {}
        vivero = inv.get("viveros") or {}

        items_validados.append({
            "inventario_id": req_item.inventario_id,
            "cantidad": req_item.cantidad,
            "nombre": planta.get("nombre_comun", "Planta"),
            "nombre_vivero": vivero.get("nombre_vivero"),
            "municipio": vivero.get("ciudad"),
            "foto_ia_url": inv.get("foto_ia_url"),
            "precio_comprador_unitario": precio_comprador,
            "precio_mayorista_unitario": round(precio_mayorista),
            "precio_unitario": round(precio_mayorista),  # compatibilidad con _obtener_desglose_pago
            "subtotal": subtotal_comprador,
            "subtotal_comprador": subtotal_comprador,
            "subtotal_mayorista": subtotal_mayorista,
            "categoria": categoria,
            "logistics_tier": tier,
        })
        total_plantas += req_item.cantidad
        total_comprador += subtotal_comprador
        total_mayorista += subtotal_mayorista
        tiers_presentes.append(tier)

    tiene_materas = any(it.get("categoria") == "materas" for it in items_validados)
    dias_entrega_minimos = 10 if tiene_materas else 5

    return {
        "ok": len(errores) == 0,
        "errores": errores,
        "items": items_validados,
        "total_plantas": total_plantas,
        "total_comprador": total_comprador,
        "total_mayorista": total_mayorista,
        "tier_logistico": _tier_mayor(tiers_presentes),
        "tiene_materas": tiene_materas,
        "dias_entrega_minimos": dias_entrega_minimos,
    }


# ═══════════════════════════════════════════════════════════
# Endpoint 1: VALIDATE CART (Fase 10.2)
# ═══════════════════════════════════════════════════════════

@router.post("/validate-cart")
async def validate_cart(req: ValidateCartRequest):
    """Re-valida items del carrito guest contra la BD."""
    db = admin()
    resultado = _validar_y_calcular_carrito(db, req.items)

    if resultado.get("error_code") == "limite_total":
        raise HTTPException(400, detail={
            "error": "limite_total",
            "mensaje": resultado["mensaje"],
        })

    if not resultado["ok"]:
        return {
            "ok": False,
            "errores": resultado["errores"],
            "items_validados": resultado["items"],
            "total_plantas": resultado["total_plantas"],
            "total_cop": resultado["total_comprador"],
        }

    return {
        "ok": True,
        "items": resultado["items"],
        "total_plantas": resultado["total_plantas"],
        "total_cop": resultado["total_comprador"],
        "tier_logistico": resultado["tier_logistico"],
        "tiene_materas": resultado["tiene_materas"],
        "dias_entrega_minimos": resultado["dias_entrega_minimos"],
    }


# ═══════════════════════════════════════════════════════════
# Endpoint 2: CALCULAR FLETE (Fase 10.2)
# ═══════════════════════════════════════════════════════════

@router.post("/calcular-flete")
async def calcular_flete(req: CalcularFleteRequest):
    db = admin()
    inv_ids = [it.inventario_id for it in req.items]

    tier_resp = db.table("inventario").select(
        "inventario_id, logistics_tier"
    ).in_("inventario_id", inv_ids).execute()
    tiers = [r.get("logistics_tier", "M") for r in (tier_resp.data or [])]

    if not tiers:
        raise HTTPException(400, detail="No se encontraron productos válidos")

    tier_mayor = _tier_mayor(tiers)

    if tier_mayor not in ("S", "M"):
        raise HTTPException(400, detail="Este pedido requiere cuenta empresa")

    ciudad = req.ciudad.strip()
    if not ciudad:
        raise HTTPException(400, detail="Ciudad requerida")

    flete_cop = _calcular_flete_por_tarifa(db, ciudad, tier_mayor)
    zona = _resolver_zona(ciudad)

    return {
        "ok": True,
        "ciudad": ciudad,
        "tier": tier_mayor,
        "zona": zona,
        "flete_cop": flete_cop,
        "dias_entrega_estimados": 5 if zona == 1 else 7,
    }


# ═══════════════════════════════════════════════════════════
# Endpoint 3: CREATE ORDER (Fase 10.3 + 10.4 + 10.4 FIX + 10.4 SHOW)
# ═══════════════════════════════════════════════════════════

def _liberar_reservas_expiradas(db) -> int:
    """Limpieza pasiva: libera stock de cotizaciones guest expiradas sin pago.

    Se ejecuta al inicio de cada create-order. Con volumen bajo B2C (5-10/día)
    esto reemplaza un cron dedicado.
    """
    try:
        # Buscar cotizaciones guest expiradas sin pago aprobado
        ahora_iso = datetime.now(timezone.utc).isoformat()
        cot_resp = db.table("cotizaciones").select(
            "cotizacion_id, items, estado"
        ).eq("estado", "convertida").lt("fecha_vencimiento", ahora_iso).execute()

        if not cot_resp.data:
            return 0

        cot_ids = [c["cotizacion_id"] for c in cot_resp.data]

        # Verificar cuáles NO tienen pago aprobado
        txn_resp = db.table("transacciones_b2b").select(
            "cotizacion_id, transaccion_id"
        ).in_("cotizacion_id", cot_ids).execute()
        cot_txn_map = {t["cotizacion_id"]: t["transaccion_id"] for t in (txn_resp.data or [])}

        expiradas_sin_pago = []
        for cot in cot_resp.data:
            cot_id = cot["cotizacion_id"]
            txn_id = cot_txn_map.get(cot_id)
            if not txn_id:
                expiradas_sin_pago.append(cot)
                continue
            pago = db.table("pagos").select("estado_pago").eq(
                "transaccion_id", txn_id
            ).eq("estado_pago", "aprobado").limit(1).execute()
            if not pago.data:
                expiradas_sin_pago.append(cot)

        # Para cada expirada, liberar stock y marcar como vencida
        liberadas = 0
        for cot in expiradas_sin_pago:
            for it in cot.get("items") or []:
                try:
                    db.rpc("liberar_stock", {
                        "p_inventario_id": it.get("inventario_id"),
                        "p_cantidad": it.get("cantidad"),
                    }).execute()
                except Exception as e:
                    logger.warning(f"Error liberando stock cot {cot['cotizacion_id']}: {e}")

            db.table("cotizaciones").update({
                "estado": "vencida",
            }).eq("cotizacion_id", cot["cotizacion_id"]).execute()
            liberadas += 1

        return liberadas
    except Exception as e:
        logger.exception(f"Error en limpieza pasiva: {e}")
        return 0


def _upsert_cliente_guest(
    db,
    nombre: str,
    tipo_documento: str,
    num_documento: str,
    email: str,
    whatsapp: str,
) -> tuple[int, bool]:
    """Busca o crea cliente_guest por email normalizado.

    Retorna (cliente_id, es_compra_recurrente).
    Recurrente = tiene 1+ pagos previos aprobados (esta será la 2da+).
    """
    email_norm = email.strip().lower()

    # Buscar cliente guest existente por email
    existing = db.table("clientes").select(
        "cliente_id, whatsapp_numero, nombre_representante, es_guest"
    ).eq("es_guest", True).eq("email", email_norm).limit(1).execute()

    if existing.data:
        cliente_id = existing.data[0]["cliente_id"]

        # Actualizar datos si cambiaron (incluye documento por si el guest usó otro)
        db.table("clientes").update({
            "nombre_representante": nombre.strip(),
            "whatsapp_numero": whatsapp.strip(),
            "tipo_documento": tipo_documento,
            "numero_documento": num_documento.strip(),
        }).eq("cliente_id", cliente_id).execute()

        # Contar pagos previos aprobados
        pagos_previos = db.table("transacciones_b2b").select(
            "transaccion_id, pagos(estado_pago)"
        ).eq("cliente_id", cliente_id).execute()

        aprobados = 0
        for txn in (pagos_previos.data or []):
            for p in (txn.get("pagos") or []):
                if p.get("estado_pago") == "aprobado":
                    aprobados += 1
                    break

        es_recurrente = aprobados >= 1
        return (cliente_id, es_recurrente)

    # Crear cliente_guest nuevo con documento en columnas dedicadas
    new_resp = db.table("clientes").insert({
        "es_guest": True,
        "activo": True,
        "tipo_cliente": "particular",
        "nombre_representante": nombre.strip(),
        "nombre_empresa": f"Compra particular - {nombre.strip()}",
        "email": email_norm,
        "whatsapp_numero": whatsapp.strip(),
        "tipo_documento": tipo_documento,
        "numero_documento": num_documento.strip(),
        "habeas_data": True,
    }).execute()

    if not new_resp.data:
        raise HTTPException(500, detail="No se pudo crear el cliente")

    return (new_resp.data[0]["cliente_id"], False)


@router.post("/create-order", response_model=CreateOrderResponse)
async def create_order(req: CreateOrderRequest):
    """Crea el pedido guest completo + payment intent ePayco.

    Flujo:
    1. Validar aceptaciones legales
    2. Limpieza pasiva de reservas expiradas
    3. Re-validar cart
    4. Calcular flete
    5. NUEVO FIX (10.4): Detectar cliente registrado B2B si existe por email
    6. Marcar conversión si es recurrente
    7. Crear cotización
    8. Reservar stock atómicamente por item
    9. Crear transaccion_b2b + pago
    10. FASE 10.4: Aplicar descuento 12% B2B sobre SUBTOTAL
    11. Generar payload ePayco
    12. FASE 10.4 SHOW: Devolver descuento en respuesta
    13. Devolver todo al frontend
    """
    s = get_settings()
    epayco = get_epayco()
    if not epayco.is_configured:
        raise HTTPException(503, detail="Servicio de pagos no configurado")

    # 1. Aceptaciones legales
    if not req.acepta_perecedero:
        raise HTTPException(400, detail="Debés aceptar el aviso de producto perecedero")
    if not req.acepta_habeas_data:
        raise HTTPException(400, detail="Debés aceptar el tratamiento de datos personales")

    db = admin()

    # 2. Limpieza pasiva
    liberadas = _liberar_reservas_expiradas(db)
    if liberadas > 0:
        logger.info(f"[Fase 10.3] Liberadas {liberadas} reservas guest expiradas")

    # 3. Re-validar cart
    validacion = _validar_y_calcular_carrito(db, req.items)
    if not validacion["ok"]:
        raise HTTPException(400, detail={
            "error": "validacion_fallida",
            "mensaje": "Algunos productos ya no están disponibles. Volvé al carrito.",
            "errores": validacion["errores"],
        })

    items_validados = validacion["items"]
    total_comprador = validacion["total_comprador"]
    total_mayorista = validacion["total_mayorista"]
    tier_logistico = validacion["tier_logistico"]

    # 4. Calcular flete
    ciudad_norm = req.ciudad.strip()
    if not ciudad_norm:
        raise HTTPException(400, detail="Ciudad requerida")
    flete_cop = _calcular_flete_por_tarifa(db, ciudad_norm, tier_logistico)

    # ═══════════════════════════════════════════════════════════════════════
    # 5. NUEVO FIX (Fase 10.4): Detectar cliente registrado B2B
    # ═══════════════════════════════════════════════════════════════════════
    email_norm = req.email.strip().lower()
    
    # Buscar cliente REGISTRADO (no guest) con este email
    cliente_registrado = db.table("clientes").select(
        "cliente_id, es_guest, nombre_representante"
    ).eq("email", email_norm).eq("es_guest", False).limit(1).execute()
    
    if cliente_registrado.data:
        # Usar cliente registrado B2B existente
        cliente_id = cliente_registrado.data[0]["cliente_id"]
        es_recurrente = True
        
        logger.info(
            f"[Fase 10.4 FIX] Cliente registrado B2B encontrado: "
            f"{cliente_id} ({cliente_registrado.data[0].get('nombre_representante')})"
        )
        
        # Obtener datos del cliente registrado
        cliente_data = {"es_guest": False}
    else:
        # No existe registrado → crear/usar guest
        cliente_id, es_recurrente = _upsert_cliente_guest(
            db,
            nombre=req.nombre,
            tipo_documento=req.tipo_documento,
            num_documento=req.num_documento,
            email=req.email,
            whatsapp=req.whatsapp,
        )
        
        # Obtener datos del cliente (ya sea nuevo guest o guest existente)
        cliente_resp = db.table("clientes").select(
            "cliente_id, es_guest"
        ).eq("cliente_id", cliente_id).limit(1).execute()
        cliente_data = cliente_resp.data[0] if cliente_resp.data else {"es_guest": True}

    # 6. Marcar conversión si es 2da+ compra
    if es_recurrente and cliente_data.get("es_guest") == False:
        try:
            db.table("clientes").update({
                "necesita_conversion_b2b": True,
            }).eq("cliente_id", cliente_id).execute()
            logger.info(f"[Fase 10.3] Cliente {cliente_id} marcado para conversión B2B")
        except Exception as e:
            logger.warning(f"No se pudo marcar conversión de cliente {cliente_id}: {e}")

    # 7. Preparar datos de entrega
    contacto_nombre = req.nombre if req.mismo_receptor else (req.receptor_nombre or req.nombre)
    contacto_telefono = req.whatsapp if req.mismo_receptor else (req.receptor_telefono or req.whatsapp)
    direccion_completa = req.direccion.strip()
    if req.referencia and req.referencia.strip():
        direccion_completa += f" ({req.referencia.strip()})"

    fecha_venc = datetime.now(timezone.utc) + timedelta(minutes=15)

    # ═══════════════════════════════════════════════════════════════════════
    # 🔴 FASE 10.4: Aplicar descuento 12% B2B sobre SUBTOTAL (ANTES de flete)
    # ═══════════════════════════════════════════════════════════════════════
    es_b2b = not cliente_data.get("es_guest", False)
    fintech_activa = bool(get_config("fintech_activa", default=False))
    
    # Leer umbral SMLMV de config
    smlmv = float(get_config("smlmv_actual", default=1_750_905))
    umbral_smlmv = int(get_config("umbral_descuento_b2b_smlmv", default=5))
    umbral_pesos = smlmv * umbral_smlmv
    
    subtotal_con_descuento = total_comprador
    descuento_pesos = 0
    descuento_porcentaje = 0.0
    descuento_aplicado = False
    
    if es_b2b and not fintech_activa:
        # VALIDACIÓN: Solo aplicar descuento si >= 5 SMLMV
        if total_comprador >= umbral_pesos:
            descuento_pesos = int(total_comprador * 0.12)
            subtotal_con_descuento = total_comprador - descuento_pesos
            descuento_porcentaje = 12.0
            descuento_aplicado = True
            
            logger.info(
                f"[Fase 10.4] Descuento B2B 12% aplicado: "
                f"Subtotal ${total_comprador:,} >= Umbral ${umbral_pesos:,} (5 SMLMV × ${smlmv:,.0f}) "
                f"→ Descuento: ${descuento_pesos:,} → Nuevo subtotal: ${subtotal_con_descuento:,}"
            )
        else:
            logger.info(
                f"[Fase 10.4] Descuento B2B NO aplicado: "
                f"Subtotal ${total_comprador:,} < Umbral ${umbral_pesos:,} (5 SMLMV) "
                f"→ Cliente B2B pero compra pequeña"
            )
    elif es_b2b and fintech_activa:
        logger.info(
            f"[Fase 10.4] Descuento B2B delegado a fintech: "
            f"fintech_activa=true → Usar descuentos de financiamiento"
        )
    else:
        logger.info(
            f"[Fase 10.4] Descuento B2B NO aplicado: "
            f"Cliente es guest={not es_b2b}"
        )
    
    # Flete se suma al subtotal DESPUÉS del descuento
    monto_total_epayco = subtotal_con_descuento + flete_cop
    # ═════════════════════════════════════════════════════════════════════════

    # 8. Guardar cotización (con total_estimado sin flete ni descuento, para auditoría)
    cot_data = {
        "cliente_id": cliente_id,
        "estado": "convertida",  # skip aprobación viverista (compra directa guest)
        "items": items_validados,
        "total_estimado": total_comprador,  # sin flete, sin descuento (para auditoría)
        "ciudad_entrega": ciudad_norm,
        "direccion_entrega_exacta": direccion_completa,
        "contacto_nombre": contacto_nombre,
        "contacto_telefono": contacto_telefono,
        "fecha_vencimiento": fecha_venc.isoformat(),
        "notas_cliente": req.notas.strip() if req.notas else "",
        "prompt_original": f"Compra B2C guest - {req.nombre}",
        "generada_por_ia": False,
    }
    if req.fecha_entrega:
        cot_data["fecha_entrega_deseada"] = req.fecha_entrega

    cot_resp = db.table("cotizaciones").insert(cot_data).execute()
    if not cot_resp.data:
        raise HTTPException(500, detail="No se pudo crear la cotización")

    cotizacion_id = cot_resp.data[0]["cotizacion_id"]
    logger.info(f"[Fase 10.3] Cotización {cotizacion_id} creada para cliente {cliente_id}")

    # 9. Reservar stock atómicamente
    stock_reservado = []  # para rollback si algo falla después
    try:
        for it in items_validados:
            resp = db.rpc("reservar_stock_atomic", {
                "p_inventario_id": it["inventario_id"],
                "p_cantidad": it["cantidad"],
            }).execute()

            resultado_rpc = resp.data
            if resultado_rpc == -1 or resultado_rpc is None:
                # Rollback: liberar lo ya reservado + borrar cotización
                for prev in stock_reservado:
                    try:
                        db.rpc("liberar_stock", {
                            "p_inventario_id": prev["inventario_id"],
                            "p_cantidad": prev["cantidad"],
                        }).execute()
                    except Exception:
                        pass
                db.table("cotizaciones").delete().eq("cotizacion_id", cotizacion_id).execute()
                raise HTTPException(
                    400,
                    detail=f"Stock insuficiente para el producto {it['nombre']} en el último momento",
                )

            stock_reservado.append(it)
    except HTTPException:
        raise
    except Exception as e:
        # Rollback
        for prev in stock_reservado:
            try:
                db.rpc("liberar_stock", {
                    "p_inventario_id": prev["inventario_id"],
                    "p_cantidad": prev["cantidad"],
                }).execute()
            except Exception:
                pass
        db.table("cotizaciones").delete().eq("cotizacion_id", cotizacion_id).execute()
        logger.exception(f"[Fase 10.3] Error reservando stock: {e}")
        raise HTTPException(500, detail=f"Error reservando stock: {e}")

    # 10. Crear transaccion_b2b (con subtotal ya con descuento aplicado)
    # precio_total = subtotal con descuento pero SIN flete (para desglose limpio)
    comision_plataforma = subtotal_con_descuento - total_mayorista

    try:
        txn_resp = db.table("transacciones_b2b").insert({
            "cliente_id": cliente_id,
            "cotizacion_id": cotizacion_id,
            "precio_total": subtotal_con_descuento,  # CON descuento B2B si aplica, SIN flete
            "comision_plataforma": comision_plataforma,  # ajustado por descuento
            "estado": "pendiente",
        }).execute()
        if not txn_resp.data:
            raise Exception("No se pudo crear la transacción")
        transaccion_id = txn_resp.data[0]["transaccion_id"]
    except Exception as e:
        # Rollback
        for it in stock_reservado:
            try:
                db.rpc("liberar_stock", {
                    "p_inventario_id": it["inventario_id"],
                    "p_cantidad": it["cantidad"],
                }).execute()
            except Exception:
                pass
        db.table("cotizaciones").delete().eq("cotizacion_id", cotizacion_id).execute()
        logger.exception(f"[Fase 10.3] Error creando transaccion: {e}")
        raise HTTPException(500, detail=f"Error creando transacción: {e}")

    # 11. Generar payload ePayco (mismo patrón que pagos.py B2B)
    response_url = f"{s.app_base_url}/pagos/resultado"
    confirmation_url = f"{s.app_base_url}/api/pagos/confirmacion"

    try:
        checkout_req = CheckoutRequest(
            transaccion_id=transaccion_id,
            monto_cop=monto_total_epayco,  # CON flete, CON descuento (lo que se cobra)
            descripcion=f"viveroonline.com.co · Compra #{transaccion_id} ({validacion['total_plantas']} plantas)",
            nombre_cliente=req.nombre.strip(),
            telefono_cliente=req.whatsapp.strip(),
        )
        payload = epayco.build_checkout_payload(checkout_req, response_url, confirmation_url)
        referencia = payload["invoice"]
    except Exception as e:
        # Rollback
        db.table("transacciones_b2b").delete().eq("transaccion_id", transaccion_id).execute()
        for it in stock_reservado:
            try:
                db.rpc("liberar_stock", {
                    "p_inventario_id": it["inventario_id"],
                    "p_cantidad": it["cantidad"],
                }).execute()
            except Exception:
                pass
        db.table("cotizaciones").delete().eq("cotizacion_id", cotizacion_id).execute()
        logger.exception(f"[Fase 10.3] Error generando payload ePayco: {e}")
        raise HTTPException(500, detail=f"Error preparando el pago: {e}")

    # 12. Crear pago
    try:
        pago_resp = db.table("pagos").insert({
            "transaccion_id": transaccion_id,
            "monto_total": monto_total_epayco,  # CON flete, CON descuento (lo que cobra ePayco)
            "moneda": "COP",
            "estado_pago": "pendiente",
            "metodo": "epayco",
            "referencia_externa": referencia,
            "monto_viverista": total_mayorista,  # SIEMPRE precio_mayorista puro (sin markup, sin flete, sin descuento)
            "monto_plataforma": comision_plataforma,  # ajustado por descuento
        }).execute()
        if not pago_resp.data:
            raise Exception("No se pudo insertar el pago")
        pago_id = pago_resp.data[0]["pago_id"]
    except Exception as e:
        # Rollback completo
        db.table("transacciones_b2b").delete().eq("transaccion_id", transaccion_id).execute()
        for it in stock_reservado:
            try:
                db.rpc("liberar_stock", {
                    "p_inventario_id": it["inventario_id"],
                    "p_cantidad": it["cantidad"],
                }).execute()
            except Exception:
                pass
        db.table("cotizaciones").delete().eq("cotizacion_id", cotizacion_id).execute()
        logger.exception(f"[Fase 10.3] Error creando pago: {e}")
        raise HTTPException(500, detail=f"Error registrando el pago: {e}")

    logger.info(
        f"[Fase 10.3] Pedido creado OK: "
        f"cot={cotizacion_id}, txn={transaccion_id}, pago={pago_id}, "
        f"monto_epayco=${monto_total_epayco:,}, "
        f"descuento_aplicado={descuento_aplicado} (${descuento_pesos:,}), "
        f"cliente_b2b={es_b2b}, "
        f"ref={referencia}"
    )

    # ═══════════════════════════════════════════════════════════════════════
    # 🟢 FASE 10.4 SHOW: Devolver descuento en respuesta
    # ═══════════════════════════════════════════════════════════════════════
    return CreateOrderResponse(
        ok=True,
        cotizacion_id=cotizacion_id,
        transaccion_id=transaccion_id,
        pago_id=pago_id,
        referencia=referencia,
        monto_total=monto_total_epayco,
        descuento_pesos=descuento_pesos,  # ← NUEVO
        descuento_porcentaje=descuento_porcentaje,  # ← NUEVO
        monto_con_descuento=subtotal_con_descuento,  # ← NUEVO
        checkout_payload=payload,
        expires_at=fecha_venc.isoformat(),
    )
