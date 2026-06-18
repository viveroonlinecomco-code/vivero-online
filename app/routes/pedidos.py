"""Flujo de aprobación y pago de cotizaciones.

Flujo de estados:
  borrador → enviada (comprador solicita)
           → aceptada (viverista aprueba)  → checkout → pagada
           → rechazada (viverista rechaza)

Modelo de precios:
  - precio_mayorista en BD = precio BASE del viverista (lo que él recibe)
  - El comprador paga: total_cotizacion × 1.18 (18% de markup de plataforma) + flete
  - ViveroOnline retiene: total_cotizacion × 0.18
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from app.auth.deps import UserContext, require_comprador, require_viverista
from app.config import get_settings
from app.services.supabase import admin as db_admin
from app.services.whatsapp_meta import send_text_message

router = APIRouter(prefix="/api/pedidos", tags=["pedidos"])

MARKUP_PLATAFORMA = 0.18


def _get_cotizacion(db, cotizacion_id: int) -> dict:
    r = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, estado, items, total_estimado, "
        "prompt_original, notas_cliente, ciudad_entrega, fecha_entrega_deseada"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    if not r.data:
        raise HTTPException(404, "Cotización no encontrada")
    return r.data[0]


def _resumir_items(db, items: list[dict]) -> str:
    """Genera resumen legible de los items para el mensaje WhatsApp."""
    if not items:
        return "Sin items"
    inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
    inv_map = {}
    if inv_ids:
        resp = db.table("inventario").select(
            "inventario_id, plantas(nombre_comun)"
        ).in_("inventario_id", inv_ids).execute()
        inv_map = {r["inventario_id"]: (r.get("plantas") or {}).get("nombre_comun", "Planta") 
                   for r in (resp.data or [])}
    
    lineas = []
    for it in items[:5]:  # máximo 5 items en el mensaje
        nombre = inv_map.get(it.get("inventario_id"), "Planta")
        cantidad = it.get("cantidad", 0)
        lineas.append(f"  • {nombre} × {cantidad}")
    
    if len(items) > 5:
        lineas.append(f"  • ... y {len(items) - 5} más")
    
    return "\n".join(lineas)


# ═══════════ 1. COMPRADOR: Solicitar al vivero ═══════════

@router.post("/{cotizacion_id}/solicitar")
async def solicitar_aprobacion(
    cotizacion_id: int,
    user: UserContext = Depends(require_comprador),
):
    """Comprador envía el borrador al viverista para aprobación."""
    db = db_admin()
    cot = _get_cotizacion(db, cotizacion_id)

    if cot["cliente_id"] != user.cliente_id:
        raise HTTPException(403, "No podés solicitar esta cotización")
    if cot["estado"] != "borrador":
        raise HTTPException(400, f"Solo los borradores se pueden enviar. Estado actual: {cot['estado']}")
    if not cot.get("items"):
        raise HTTPException(400, "El carrito está vacío")

    db.table("cotizaciones").update({"estado": "enviada"}).eq("cotizacion_id", cotizacion_id).execute()

    # ── Crear sub-cotizaciones por vivero y notificar ─────────────────────────
    try:
        base = get_settings().app_base_url
        items = cot.get("items") or []
        nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"
        notas = cot.get("notas_cliente", "")

        # Agrupar items por vivero
        inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
        if inv_ids:
            inv_resp = db.table("inventario").select(
                "inventario_id, vivero_id"
            ).in_("inventario_id", inv_ids).execute()
            inv_vivero_map = {r["inventario_id"]: r["vivero_id"] for r in (inv_resp.data or [])}

            # Agrupar items por vivero_id
            items_por_vivero: dict[int, list] = {}
            for it in items:
                vid = inv_vivero_map.get(it.get("inventario_id"))
                if vid:
                    items_por_vivero.setdefault(vid, []).append(it)

            # Crear sub-cotización y notificar a cada vivero
            for vivero_id, vitems in items_por_vivero.items():
                total_vivero = sum(float(it.get("subtotal") or 0) for it in vitems)

                # Crear sub-cotización
                db.table("sub_cotizaciones").insert({
                    "cotizacion_id": cotizacion_id,
                    "vivero_id": vivero_id,
                    "items": vitems,
                    "total_estimado": total_vivero,
                    "estado": "pendiente",
                }).execute()

                # Notificar al viverista
                v = db.table("viveros").select(
                    "whatsapp_numero, nombre_vivero"
                ).eq("vivero_id", vivero_id).limit(1).execute()

                if not v.data or not v.data[0].get("whatsapp_numero"):
                    continue

                resumen = _resumir_items(db, vitems)
                total_comprador = round(total_vivero * (1 + MARKUP_PLATAFORMA))

                msg = (
                    f"🌿 *Nueva solicitud — ViveroOnline*\n\n"
                    f"Proyecto: *{nombre_proyecto}*\n\n"
                    f"📦 *Tus plantas solicitadas:*\n{resumen}\n\n"
                    f"💰 Tu precio: ${int(total_vivero):,} COP\n"
                    f"🛒 Comprador paga: ${total_comprador:,} COP\n"
                )
                if notas:
                    msg += f"\n📝 Notas: {notas}\n"
                msg += f"\n¿Confirmás disponibilidad?\nRespondé *APROBAR* o *RECHAZAR*"

                await send_text_message(v.data[0]["whatsapp_numero"], msg)

                # Guardar acción pendiente en sesión del viverista
                accion = {
                    "type": "aprobar_rechazar_cotizacion",
                    "confirmed": False,
                    "params": {
                        "cotizacion_id": cotizacion_id,
                        "nombre_proyecto": nombre_proyecto,
                        "total_base": int(total_vivero),
                    }
                }
                sesion = db.table("sesiones_agente").select(
                    "sesion_id"
                ).eq("whatsapp_numero", v.data[0]["whatsapp_numero"]).eq(
                    "estado", "activa"
                ).limit(1).execute()

                if sesion.data:
                    db.table("sesiones_agente").update({
                        "accion_pendiente": accion
                    }).eq("sesion_id", sesion.data[0]["sesion_id"]).execute()
                else:
                    perfil = db.table("perfiles").select("id").eq(
                        "whatsapp_numero", v.data[0]["whatsapp_numero"]
                    ).eq("rol", "viverista").limit(1).execute()
                    if perfil.data:
                        db.table("sesiones_agente").insert({
                            "whatsapp_numero": v.data[0]["whatsapp_numero"],
                            "tipo_usuario": "viverista",
                            "vivero_id": vivero_id,
                            "estado": "activa",
                            "flujo_actual": "chat",
                            "contexto_json": {"historial": []},
                            "mensajes_count": 0,
                            "fotos_procesadas": 0,
                            "accion_pendiente": accion,
                        }).execute()

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"No se pudo notificar a los viveristas: {e}")

    return {"ok": True, "estado": "enviada", "cotizacion_id": cotizacion_id}


# ═══════════ 2. VIVERISTA: Ver cotizaciones pendientes ═══════════

@router.get("/pendientes")
async def listar_pendientes(user: UserContext = Depends(require_viverista)):
    db = db_admin()
    if not user.vivero_id:
        raise HTTPException(400, "Tu perfil no está vinculado a un vivero")

    r = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, estado, items, total_estimado, "
        "prompt_original, notas_cliente, fecha_creacion"
    ).eq("estado", "enviada").execute()

    pendientes = []
    for cot in (r.data or []):
        items = cot.get("items") or []
        if not items:
            continue

        inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
        if not inv_ids:
            continue

        inv_resp = db.table("inventario").select(
            "inventario_id, vivero_id, plantas(nombre_comun)"
        ).in_("inventario_id", inv_ids).execute()
        inv_map = {row["inventario_id"]: row for row in (inv_resp.data or [])}

        mis_items = []
        for it in items:
            inv = inv_map.get(it.get("inventario_id"))
            if not inv or inv.get("vivero_id") != user.vivero_id:
                continue
            planta = inv.get("plantas") or {}
            mis_items.append({
                **it,
                "vivero_id": user.vivero_id,
                "nombre_comun": planta.get("nombre_comun") or f"Item #{it.get('inventario_id')}",
            })

        if not mis_items:
            continue

        cliente = db.table("clientes").select(
            "nombre_empresa, nombre_representante"
        ).eq("cliente_id", cot["cliente_id"]).limit(1).execute()
        nombre_comprador = "Comprador"
        if cliente.data:
            nombre_comprador = (
                cliente.data[0].get("nombre_representante") or
                cliente.data[0].get("nombre_empresa") or
                "Comprador"
            )

        total_base = float(cot.get("total_estimado") or 0)
        pendientes.append({
            "cotizacion_id": cot["cotizacion_id"],
            "nombre_proyecto": cot.get("prompt_original") or f"Cotización #{cot['cotizacion_id']}",
            "nombre_comprador": nombre_comprador,
            "estado": cot["estado"],
            "items": mis_items,
            "total_estimado": total_base,
            "total_comprador": round(total_base * (1 + MARKUP_PLATAFORMA)),
            "notas_cliente": cot.get("notas_cliente"),
            "fecha_creacion": str(cot.get("fecha_creacion") or ""),
        })

    return {"ok": True, "pendientes": pendientes, "total": len(pendientes)}


# ═══════════ 3. VIVERISTA: Aprobar cotización ═══════════

class RechazarReq(BaseModel):
    motivo: Optional[str] = None


async def aprobar_subcotizacion_vivero(db, cotizacion_id: int, vivero_id: int) -> dict:
    """Lógica de negocio compartida para aprobar la parte de un vivero dentro
    de una cotización (multi-vivero o legacy de un solo vivero).

    Usada tanto por el endpoint HTTP /aprobar como por el handler de WhatsApp
    APROBAR, para que ambos caminos respeten exactamente las mismas reglas
    de sub_cotizaciones (AJUSTE 18 jun: antes el camino de WhatsApp duplicaba
    esta lógica sin tocar sub_cotizaciones, rompiendo el flujo multi-vivero).

    GUARD CLAUSE (AJUSTE 18 jun): el UPDATE de la cotización principal es
    condicional y atómico — incluye `.eq("estado", "enviada")` en la cláusula
    del propio UPDATE, no solo en una validación previa. Esto cierra la
    ventana de carrera entre el SELECT de validación y el UPDATE (ej. el cron
    de vencimiento corriendo justo cuando el viverista aprueba): si otro
    proceso ya cambió el estado entre medio, este UPDATE no afecta ninguna
    fila y `resp.data` viene vacío, así que lo detectamos y devolvemos un
    resultado claro en vez de pisar silenciosamente el cambio del otro proceso.

    Retorna dict con "ok": False y "motivo" si no se pudo aplicar (cotización
    no encontrada, no tiene items de este vivero, o ya cambió de estado).
    """
    from datetime import datetime, timezone, timedelta

    cot = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, estado, items, total_estimado, prompt_original"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    if not cot.data:
        return {"ok": False, "motivo": "no_encontrada"}
    cot = cot.data[0]

    items = cot.get("items") or []
    inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
    if inv_ids:
        inv_resp = db.table("inventario").select("inventario_id, vivero_id").in_(
            "inventario_id", inv_ids
        ).execute()
        if not any(r.get("vivero_id") == vivero_id for r in (inv_resp.data or [])):
            return {"ok": False, "motivo": "sin_items_de_este_vivero"}

    # ── Actualizar sub-cotización de este vivero (atómico: solo si seguía pendiente) ──
    sub_update = db.table("sub_cotizaciones").update({
        "estado": "aprobada",
        "fecha_respuesta": datetime.now(timezone.utc).isoformat(),
    }).eq("cotizacion_id", cotizacion_id).eq("vivero_id", vivero_id).eq(
        "estado", "pendiente"
    ).execute()

    hubo_sub = bool(sub_update.data)
    if not hubo_sub:
        # Puede ser flujo legacy sin sub_cotizaciones, o ya fue procesada antes.
        existe_sub = db.table("sub_cotizaciones").select("sub_cotizacion_id, estado").eq(
            "cotizacion_id", cotizacion_id
        ).eq("vivero_id", vivero_id).limit(1).execute()
        if existe_sub.data and existe_sub.data[0]["estado"] != "pendiente":
            return {"ok": False, "motivo": "ya_procesada", "estado_actual": existe_sub.data[0]["estado"]}

    # ── Verificar si TODAS las sub-cotizaciones están aprobadas ──────────────
    pendientes = db.table("sub_cotizaciones").select("sub_cotizacion_id").eq(
        "cotizacion_id", cotizacion_id
    ).in_("estado", ["pendiente", "rechazada"]).execute()

    todas_aprobadas = len(pendientes.data or []) == 0

    if todas_aprobadas:
        fecha_vencimiento = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        # UPDATE condicional atómico: solo aplica si la cotización seguía en
        # "enviada". Si ya la venció el cron o la cerró otro proceso, esto no
        # afecta ninguna fila y lo detectamos por resp.data vacío.
        update_resp = db.table("cotizaciones").update({
            "estado": "aceptada",
            "fecha_vencimiento": fecha_vencimiento,
        }).eq("cotizacion_id", cotizacion_id).eq("estado", "enviada").execute()

        if not update_resp.data:
            return {
                "ok": False,
                "motivo": "estado_cambio_antes_del_update",
                "cotizacion_id": cotizacion_id,
            }

        # Notificar al comprador solo cuando TODOS aprobaron
        try:
            base = get_settings().app_base_url
            cliente = db.table("clientes").select("whatsapp_numero").eq(
                "cliente_id", cot["cliente_id"]
            ).limit(1).execute()

            if cliente.data and cliente.data[0].get("whatsapp_numero"):
                total_base = int(float(cot.get("total_estimado") or 0))
                total_comprador = round(total_base * (1 + MARKUP_PLATAFORMA))
                nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"
                msg = (
                    f"✅ *¡Tu solicitud fue aprobada! — ViveroOnline*\n\n"
                    f"Proyecto: *{nombre_proyecto}*\n"
                    f"Total a pagar: *${total_comprador:,} COP*\n\n"
                    f"Todos los viveros confirmaron disponibilidad.\n"
                    f"Ingresá a tu panel para completar el pago:\n"
                    f"{base}/comprador"
                )
                await send_text_message(cliente.data[0]["whatsapp_numero"], msg)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"No se pudo notificar al comprador: {e}")

        return {
            "ok": True,
            "estado": "aceptada",
            "cotizacion_id": cotizacion_id,
            "nombre_proyecto": cot.get("prompt_original") or f"Cotización #{cotizacion_id}",
        }
    else:
        # Hay sub-cotizaciones aún pendientes
        aprobadas = db.table("sub_cotizaciones").select("sub_cotizacion_id").eq(
            "cotizacion_id", cotizacion_id
        ).eq("estado", "aprobada").execute()
        total_aprobadas = len(aprobadas.data or [])
        total_subs = db.table("sub_cotizaciones").select("sub_cotizacion_id").eq(
            "cotizacion_id", cotizacion_id
        ).execute()
        total = len(total_subs.data or [])
        return {
            "ok": True,
            "estado": "parcialmente_aprobada",
            "aprobadas": total_aprobadas,
            "total_viveros": total,
            "cotizacion_id": cotizacion_id,
            "nombre_proyecto": cot.get("prompt_original") or f"Cotización #{cotizacion_id}",
        }


@router.post("/{cotizacion_id}/aprobar")
async def aprobar_cotizacion(cotizacion_id: int, user: UserContext = Depends(require_viverista)):
    db = db_admin()
    resultado = await aprobar_subcotizacion_vivero(db, cotizacion_id, user.vivero_id)

    if not resultado["ok"]:
        motivo = resultado.get("motivo")
        if motivo == "no_encontrada":
            raise HTTPException(404, "Cotización no encontrada")
        if motivo == "sin_items_de_este_vivero":
            raise HTTPException(403, "Esta cotización no contiene items de tu vivero")
        if motivo == "ya_procesada":
            raise HTTPException(
                400, f"Esta cotización ya fue procesada. Estado: {resultado.get('estado_actual')}"
            )
        if motivo == "estado_cambio_antes_del_update":
            raise HTTPException(
                409, "La cotización cambió de estado justo antes de confirmar tu aprobación "
                     "(por ejemplo, venció). Revisá el estado actual antes de reintentar."
            )
        raise HTTPException(400, "No se pudo aprobar la cotización")

    return resultado


# ═══════════ 4. VIVERISTA: Rechazar cotización ═══════════

async def rechazar_subcotizacion_vivero(db, cotizacion_id: int, vivero_id: int, motivo: str | None = None) -> dict:
    """Lógica de negocio compartida para rechazar la parte de un vivero
    dentro de una cotización. Misma lógica usada por el endpoint HTTP
    /rechazar y por el handler de WhatsApp RECHAZAR (AJUSTE 18 jun, ver
    docstring de aprobar_subcotizacion_vivero para el contexto completo).

    GUARD CLAUSE: el UPDATE de sub_cotizaciones es condicional (.eq("estado",
    "pendiente")) para no rechazar dos veces ni pisar una aprobación que ya
    haya ocurrido. El UPDATE final de la cotización principal a "rechazada"
    también es condicional sobre que no haya quedado ninguna sub-cotización
    aprobada (evita marcar como rechazada una cotización que en paralelo
    fue aprobada por todos los demás viveros).
    """
    from datetime import datetime, timezone

    cot = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, estado, items, prompt_original"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()
    if not cot.data:
        return {"ok": False, "motivo": "no_encontrada"}
    cot = cot.data[0]

    items = cot.get("items") or []
    inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]
    if inv_ids:
        inv_resp = db.table("inventario").select("inventario_id, vivero_id").in_(
            "inventario_id", inv_ids
        ).execute()
        if not any(r.get("vivero_id") == vivero_id for r in (inv_resp.data or [])):
            return {"ok": False, "motivo": "sin_items_de_este_vivero"}

    # ── Marcar sub-cotización de este vivero como rechazada (atómico) ────────
    # Se encadena .select("items") tras el update porque Supabase documenta
    # que .select() después de update() es necesario para garantizar qué
    # columnas vienen en la fila devuelta — no alcanza con que "estado" haya
    # estado en el dict del update.
    sub_update = db.table("sub_cotizaciones").update({
        "estado": "rechazada",
        "notas_rechazo": motivo or "Rechazada por el viverista",
        "fecha_respuesta": datetime.now(timezone.utc).isoformat(),
    }).eq("cotizacion_id", cotizacion_id).eq("vivero_id", vivero_id).eq(
        "estado", "pendiente"
    ).select("items").execute()

    if sub_update.data:
        items_rechazados = sub_update.data[0].get("items") or []
        alternativas = []

        for it in items_rechazados:
            inv_id = it.get("inventario_id")
            cantidad = it.get("cantidad", 1)
            if not inv_id:
                continue

            alt = db.rpc("buscar_vivero_alternativo", {
                "p_inventario_id": inv_id,
                "p_cantidad": cantidad,
                "p_vivero_excluir": vivero_id,
            }).execute()

            if alt.data:
                alternativas.append({
                    "inventario_original": inv_id,
                    "inventario_alternativo": alt.data[0]["inventario_id"],
                    "vivero_alternativo_id": alt.data[0]["vivero_id"],
                    "nombre_vivero": alt.data[0]["nombre_vivero"],
                    "precio_mayorista": float(alt.data[0]["precio_mayorista"]),
                })

        nombre_proyecto = cot.get("prompt_original") or f"Cotización #{cotizacion_id}"

        # ── Notificar al comprador con alternativa si existe ──────────────────
        try:
            base = get_settings().app_base_url
            cliente = db.table("clientes").select("whatsapp_numero").eq(
                "cliente_id", cot["cliente_id"]
            ).limit(1).execute()

            if cliente.data and cliente.data[0].get("whatsapp_numero"):
                if alternativas:
                    nombres_alt = list({a["nombre_vivero"] for a in alternativas})
                    msg = (
                        f"⚠️ *Un vivero no tiene disponibilidad — ViveroOnline*\n\n"
                        f"Proyecto: *{nombre_proyecto}*\n\n"
                        f"Encontramos las mismas plantas en: *{', '.join(nombres_alt)}*\n\n"
                        f"Ingresá a tu panel para confirmar el cambio:\n"
                        f"{base}/comprador"
                    )
                else:
                    motivo_txt = f"\nMotivo: {motivo}" if motivo else ""
                    msg = (
                        f"❌ *Solicitud no disponible — ViveroOnline*\n\n"
                        f"Proyecto: {nombre_proyecto}{motivo_txt}\n\n"
                        f"No encontramos stock disponible en otro vivero.\n"
                        f"Podés buscar alternativas en el marketplace:\n"
                        f"{base}/marketplace"
                    )
                await send_text_message(cliente.data[0]["whatsapp_numero"], msg)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"No se pudo notificar al comprador: {e}")

        # Si no hay más sub-cotizaciones pendientes y ninguna fue aprobada
        # → marcar cotización principal como rechazada (condicional: solo si
        # sigue en "enviada", para no pisar un estado que ya cambió en paralelo)
        otras_pendientes = db.table("sub_cotizaciones").select("sub_cotizacion_id").eq(
            "cotizacion_id", cotizacion_id
        ).eq("estado", "pendiente").execute()

        if not otras_pendientes.data:
            aprobadas = db.table("sub_cotizaciones").select("sub_cotizacion_id").eq(
                "cotizacion_id", cotizacion_id
            ).eq("estado", "aprobada").execute()
            if not aprobadas.data:
                db.table("cotizaciones").update({
                    "estado": "rechazada",
                    "notas_agente": motivo or "Rechazada por todos los viveristas",
                }).eq("cotizacion_id", cotizacion_id).eq("estado", "enviada").execute()

        return {
            "ok": True,
            "estado": "rechazada",
            "alternativas": alternativas,
            "cotizacion_id": cotizacion_id,
            "nombre_proyecto": nombre_proyecto,
        }

    # No se pudo actualizar ninguna sub-cotización pendiente: o no existe
    # (flujo legacy de un solo vivero) o ya fue procesada antes.
    existe_sub = db.table("sub_cotizaciones").select("sub_cotizacion_id, estado").eq(
        "cotizacion_id", cotizacion_id
    ).eq("vivero_id", vivero_id).limit(1).execute()

    if existe_sub.data:
        return {"ok": False, "motivo": "ya_procesada", "estado_actual": existe_sub.data[0]["estado"]}

    # Flujo legacy sin sub_cotizaciones — UPDATE condicional sobre "enviada"
    update_resp = db.table("cotizaciones").update({
        "estado": "rechazada",
        "notas_agente": motivo or "Rechazada por el viverista",
    }).eq("cotizacion_id", cotizacion_id).eq("estado", "enviada").execute()

    if not update_resp.data:
        return {"ok": False, "motivo": "estado_cambio_antes_del_update", "cotizacion_id": cotizacion_id}

    return {
        "ok": True,
        "estado": "rechazada",
        "alternativas": [],
        "cotizacion_id": cotizacion_id,
        "nombre_proyecto": cot.get("prompt_original") or f"Cotización #{cotizacion_id}",
    }


@router.post("/{cotizacion_id}/rechazar")
async def rechazar_cotizacion(
    cotizacion_id: int, req: RechazarReq, user: UserContext = Depends(require_viverista)
):
    db = db_admin()
    resultado = await rechazar_subcotizacion_vivero(db, cotizacion_id, user.vivero_id, req.motivo)

    if not resultado["ok"]:
        motivo = resultado.get("motivo")
        if motivo == "no_encontrada":
            raise HTTPException(404, "Cotización no encontrada")
        if motivo == "sin_items_de_este_vivero":
            raise HTTPException(403, "Esta cotización no contiene items de tu vivero")
        if motivo == "ya_procesada":
            raise HTTPException(
                400, f"Esta cotización ya fue procesada. Estado: {resultado.get('estado_actual')}"
            )
        if motivo == "estado_cambio_antes_del_update":
            raise HTTPException(
                409, "La cotización cambió de estado justo antes de confirmar tu rechazo "
                     "(por ejemplo, venció). Revisá el estado actual antes de reintentar."
            )
        raise HTTPException(400, "No se pudo rechazar la cotización")

    return resultado


# ═══════════ 5. COMPRADOR: Calcular flete antes del pago ═══════════

@router.get("/{cotizacion_id}/calcular-flete")
async def calcular_flete_cotizacion(
    cotizacion_id: int,
    ciudad: str,
    user: UserContext = Depends(require_comprador),
):
    db = db_admin()

    cot = db.table("cotizaciones").select(
        "cotizacion_id, cliente_id, estado"
    ).eq("cotizacion_id", cotizacion_id).limit(1).execute()

    if not cot.data:
        raise HTTPException(404, "Cotización no encontrada")
    if cot.data[0]["cliente_id"] != user.cliente_id:
        raise HTTPException(403, "No podés ver esta cotización")

    FALLBACK = {
        "ok": True, "tier": "M", "zona": "sabana_entre_municipios",
        "precio_base": 180000, "fee_carga_viva": 18000, "total_flete": 198000, "detalle": [],
    }

    try:
        db = db_admin()

        # 1. Obtener zona de la ciudad
        zona_resp = db.table("ciudades_zonas").select("zona").eq("ciudad", ciudad).limit(1).execute()
        zona = zona_resp.data[0]["zona"] if zona_resp.data else "sabana_entre_municipios"

        # 2. Obtener tier máximo de los items de la cotización
        cot_items = db.table("cotizaciones").select("items").eq("cotizacion_id", cotizacion_id).limit(1).execute()
        items = cot_items.data[0].get("items", []) if cot_items.data else []
        inv_ids = [it.get("inventario_id") for it in items if it.get("inventario_id")]

        tier = "M"
        num_viveros = 1
        if inv_ids:
            inv_resp = db.table("inventario").select("logistics_tier, vivero_id").in_("inventario_id", inv_ids).execute()
            tier_orden = {"XL": 4, "L": 3, "M": 2, "S": 1}
            tiers = [r.get("logistics_tier", "M") for r in (inv_resp.data or [])]
            if tiers:
                tier = max(tiers, key=lambda t: tier_orden.get(t, 2))
            viveros = {r.get("vivero_id") for r in (inv_resp.data or []) if r.get("vivero_id")}
            num_viveros = len(viveros) if viveros else 1

        # 3. Obtener precio base
        tarifa_resp = db.table("tarifas_logistica").select("precio_cop").eq("zona", zona).eq("tier_base", tier).limit(1).execute()
        precio_base = tarifa_resp.data[0]["precio_cop"] if tarifa_resp.data else 180000

        # 4. Calcular fee y recargo
        fee = round(precio_base * 0.10)
        recargos = {"S": 40000, "M": 60000, "L": 90000, "XL": 120000}
        recargo = (num_viveros - 1) * recargos.get(tier, 60000) if num_viveros > 1 else 0
        total = precio_base + fee + recargo

        return {
            "ok":             True,
            "tier":           tier,
            "zona":           zona,
            "precio_base":    precio_base,
            "fee_carga_viva": fee,
            "total_flete":    total,
            "detalle":        [],
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"Error calculando flete ciudad={ciudad}: {e}")
        return FALLBACK


# ═══════════ 6. COMPRADOR: Iniciar pago ═══════════

class CheckoutReq(BaseModel):
    ciudad_entrega:           Optional[str] = None
    fecha_entrega_deseada:    Optional[str] = None
    notas:                    Optional[str] = None
    direccion_entrega_exacta: Optional[str] = None
    contacto_nombre:          Optional[str] = None
    contacto_telefono:        Optional[str] = None
    tipo_vehiculo:            Optional[str] = None
    ventana_inicio:           Optional[str] = None
    ventana_fin:              Optional[str] = None
    flete_cop:                Optional[int] = None


@router.post("/{cotizacion_id}/checkout")
async def iniciar_checkout(
    cotizacion_id: int, req: CheckoutReq, user: UserContext = Depends(require_comprador)
):
    from app.services.epayco import get_epayco, CheckoutRequest

    db = db_admin()
    cot = _get_cotizacion(db, cotizacion_id)

    if cot["cliente_id"] != user.cliente_id:
        raise HTTPException(403, "No podés pagar esta cotización")

    if cot["estado"] not in ("aceptada", "convertida"):
        raise HTTPException(400, f"Solo se pueden pagar cotizaciones aceptadas. Estado: {cot['estado']}")

    s = get_settings()
    epayco = get_epayco()
    if not epayco.is_configured:
        raise HTTPException(503, "Servicio de pagos no configurado")

    update_data: dict = {}
    if req.ciudad_entrega:           update_data["ciudad_entrega"]           = req.ciudad_entrega
    if req.fecha_entrega_deseada:    update_data["fecha_entrega_deseada"]    = req.fecha_entrega_deseada
    if req.notas:                    update_data["notas_cliente"]             = req.notas
    if req.direccion_entrega_exacta: update_data["direccion_entrega_exacta"] = req.direccion_entrega_exacta
    if req.contacto_nombre:          update_data["contacto_nombre"]           = req.contacto_nombre
    if req.contacto_telefono:        update_data["contacto_telefono"]         = req.contacto_telefono
    if req.tipo_vehiculo:            update_data["tipo_vehiculo"]             = req.tipo_vehiculo
    if req.ventana_inicio:           update_data["ventana_inicio"]            = req.ventana_inicio
    if req.ventana_fin:              update_data["ventana_fin"]               = req.ventana_fin
    if update_data:
        db.table("cotizaciones").update(update_data).eq("cotizacion_id", cotizacion_id).execute()

    total_viverista  = float(cot.get("total_estimado") or 0)
    if total_viverista <= 0:
        raise HTTPException(400, "El total de la cotización es inválido")

    monto_plantas    = round(total_viverista * (1 + MARKUP_PLATAFORMA))
    monto_plataforma = monto_plantas - round(total_viverista)
    flete_cop        = int(req.flete_cop or 0)
    monto_cop        = monto_plantas + flete_cop

    cliente = db.table("clientes").select(
        "nombre_empresa, nombre_representante, whatsapp_numero"
    ).eq("cliente_id", user.cliente_id).limit(1).execute()
    cli   = cliente.data[0] if cliente.data else {}
    nombre = cli.get("nombre_representante") or cli.get("nombre_empresa") or "Cliente"

    items = cot.get("items") or []

    transaccion_id = None
    if cot.get("estado") == "convertida":
        txn_existente = db.table("transacciones_b2b").select(
            "transaccion_id, estado"
        ).eq("cotizacion_id", cotizacion_id).eq("estado", "pendiente").limit(1).execute()
        if txn_existente.data:
            transaccion_id = txn_existente.data[0]["transaccion_id"]

    if not transaccion_id:
        txn_resp = db.table("transacciones_b2b").insert({
            "cliente_id":          user.cliente_id,
            "inventario_id":       items[0].get("inventario_id") if items else None,
            "cantidad":            sum(it.get("cantidad", 0) for it in items),
            "precio_unitario":     float(items[0].get("precio_unitario", 0)) if items else 0,
            "precio_total":        monto_cop,
            "comision_plataforma": monto_plataforma,
            "porcentaje_comision": MARKUP_PLATAFORMA * 100,
            "estado":              "pendiente",
            "cotizacion_id":       cotizacion_id,
        }).execute()

        if not txn_resp.data:
            raise HTTPException(500, "No se pudo crear la transacción")

        transaccion_id = txn_resp.data[0]["transaccion_id"]

        db.table("cotizaciones").update({
            "estado":           "convertida",
            "fecha_conversion": datetime.utcnow().isoformat(),
            "transaccion_id":   transaccion_id,
        }).eq("cotizacion_id", cotizacion_id).execute()

    checkout_req = CheckoutRequest(
        transaccion_id=transaccion_id,
        monto_cop=monto_cop,
        descripcion=f"ViveroOnline · {cot.get('prompt_original') or f'Pedido #{cotizacion_id}'}",
        nombre_cliente=nombre,
        telefono_cliente=cli.get("whatsapp_numero"),
    )
    response_url     = f"{s.app_base_url}/pagos/resultado"
    confirmation_url = f"{s.app_base_url}/api/pagos/confirmacion"
    payload = epayco.build_checkout_payload(checkout_req, response_url, confirmation_url)

    pago_resp = db.table("pagos").insert({
        "transaccion_id":     transaccion_id,
        "monto_total":        monto_cop,
        "moneda":             "COP",
        "estado_pago":        "pendiente",
        "metodo":             "epayco",
        "referencia_externa": payload["invoice"],
        "monto_viverista":    round(total_viverista),
        "monto_plataforma":   monto_plataforma,
    }).execute()

    pago_id = pago_resp.data[0]["pago_id"] if pago_resp.data else None

    return {
        "ok":               True,
        "pago_id":          pago_id,
        "transaccion_id":   transaccion_id,
        "cotizacion_id":    cotizacion_id,
        "referencia":       payload["invoice"],
        "checkout_payload": payload,
        "monto_plantas":    monto_plantas,
        "flete_cop":        flete_cop,
        "monto_cop":        monto_cop,
        "monto_viverista":  round(total_viverista),
        "monto_plataforma": monto_plataforma,
    }


# ═══════════════════════════════════════════════════════════
# CRON — Vencer cotizaciones expiradas
# ═══════════════════════════════════════════════════════════

@router.post("/cron/vencer-cotizaciones")
async def vencer_cotizaciones_cron(request: Request):
    """Vence cotizaciones expiradas. Llamado por cron-job.org cada hora."""
    import os
    from fastapi import Request
    cron_secret = os.getenv("CRON_SECRET", "")
    if cron_secret:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {cron_secret}":
            raise HTTPException(status_code=401, detail="No autorizado")

    db = db_admin()
    try:
        result = db.rpc("vencer_cotizaciones_expiradas").execute()
        total = result.data[0] if result.data else 0
        return {"ok": True, "vencidas": total}
    except Exception as e:
        return {"ok": False, "error": str(e)}
