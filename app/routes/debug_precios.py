"""Endpoint TEMPORAL de debug para diagnosticar el motor de precios.

⚠️⚠️⚠️ CRÍTICO: ELIMINAR este archivo después de resolver el bug de Fase 4.
⚠️⚠️⚠️ Este endpoint NO tiene autenticación por decisión temporal para diagnóstico.
⚠️⚠️⚠️ Expone precios internos, márgenes y configuración. NO dejar en producción.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

from app.services.supabase import admin as db_admin
from app.services.precios import calcular_precios_pedido
from app.services.config_global import (
    get_config,
    get_matriz_comercial,
    get_markup_categoria,
    get_descuento_b2b,
    _MATRIZ_DEFAULT,
)

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/precios/{cotizacion_id}")
async def debug_precios(cotizacion_id: int):
    """TEMPORAL - sin auth para diagnóstico rápido. ELIMINAR después."""
    db = db_admin()

    cot = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, items, total_estimado, estado"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

    if not cot.data:
        raise HTTPException(404, "Cotización no encontrada")

    cot_row = cot.data[0]
    items = cot_row.get("items") or []

    cliente = db.table("clientes").select(
        "cliente_id, es_guest, tipo_cliente, activo, nombre_empresa"
    ).eq("cliente_id", cot_row["cliente_id"]).limit(1).execute()
    cliente_data = cliente.data[0] if cliente.data else {"cliente_id": cot_row["cliente_id"]}

    matriz_actual = get_matriz_comercial()
    fintech_activa = get_config("fintech_activa", default=False)
    smlmv = get_config("smlmv_actual", default=1_750_905)
    umbral_smlmv = get_config("umbral_descuento_b2b_smlmv", default=5)
    comision_plataforma = get_config("comision_plataforma", default=None)

    diagnostico_items = []
    for item in items:
        inv_id = item.get("inventario_id")
        if not inv_id:
            continue

        try:
            cat_resp = db.rpc("get_categoria_producto", {
                "p_inventario_id": inv_id
            }).execute()
            categoria_sql = str(cat_resp.data) if cat_resp.data else "ERROR_NO_DATA"
        except Exception as e:
            categoria_sql = f"EXCEPTION: {e}"

        inv_data = db.table("inventario").select(
            "precio_mayorista, categoria_producto, planta_id"
        ).eq("inventario_id", inv_id).limit(1).execute()

        precio_mayorista = float(inv_data.data[0]["precio_mayorista"]) if inv_data.data else 0
        override_inv = inv_data.data[0].get("categoria_producto") if inv_data.data else None
        planta_id = inv_data.data[0].get("planta_id") if inv_data.data else None

        categoria_planta = None
        if planta_id:
            pl = db.table("plantas").select("categoria_producto, nombre_comun").eq(
                "planta_id", planta_id
            ).limit(1).execute()
            categoria_planta = pl.data[0].get("categoria_producto") if pl.data else None

        markup_efectivo = get_markup_categoria(categoria_sql)
        descuento_b2b_inmediato = get_descuento_b2b(categoria_sql, "inmediato")

        cantidad = int(item.get("cantidad", 1))
        precio_vitrina_esperado = precio_mayorista * (1 + markup_efectivo)
        subtotal_vitrina_esperado = precio_vitrina_esperado * cantidad

        precio_unitario_jsonb = item.get("precio_unitario")
        subtotal_jsonb = item.get("subtotal")

        diagnostico_items.append({
            "inventario_id": inv_id,
            "cantidad": cantidad,
            "precio_mayorista_bd": precio_mayorista,
            "precio_unitario_jsonb_cotizacion": precio_unitario_jsonb,
            "subtotal_jsonb_cotizacion": subtotal_jsonb,
            "categoria_via_sql_rpc": categoria_sql,
            "categoria_override_inventario": override_inv,
            "categoria_default_plantas": categoria_planta,
            "markup_devuelto_por_get_markup": markup_efectivo,
            "descuento_b2b_inmediato_devuelto": descuento_b2b_inmediato,
            "vitrina_unitaria_esperada": round(precio_vitrina_esperado, 2),
            "subtotal_vitrina_esperado": round(subtotal_vitrina_esperado, 2),
        })

    try:
        resultado_motor = calcular_precios_pedido(
            cliente=cliente_data,
            items=items,
            plazo="inmediato",
        )
    except Exception as e:
        resultado_motor = {"error": f"Excepción en motor: {str(e)}", "type": str(type(e).__name__)}

    return {
        "warning": "ENDPOINT TEMPORAL SIN AUTENTICACION - ELIMINAR DESPUES",
        "cotizacion_id": cotizacion_id,
        "cotizacion_estado": cot_row.get("estado"),
        "cliente": cliente_data,
        "items_del_carrito": items,
        "estado_configuracion_global": {
            "comision_plataforma": comision_plataforma,
            "fintech_activa": fintech_activa,
            "smlmv_actual": smlmv,
            "umbral_descuento_b2b_smlmv": umbral_smlmv,
            "matriz_comercial_leida": matriz_actual,
            "es_matriz_igual_a_default": matriz_actual == _MATRIZ_DEFAULT,
        },
        "diagnostico_por_item": diagnostico_items,
        "resultado_motor_calcular_precios_pedido": resultado_motor,
    }
