"""Webhook y rutas para Bot WhatsApp — VERSIÓN CORREGIDA 19 AGO.

Gestiona:
- Webhook POST /api/whatsapp/webhook (recibe mensajes)
- Sesiones por usuario (chat context)
- Flujo LangGraph para compradores/viveristas
- Tickets de soporte
- Consultas de precio (BI/B2C)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.services.supabase import admin
from app.services.whatsapp_meta import (
    send_text_message,
    verify_signature,
    procesar_consulta_precio_producto,
)
from app.routes.ticket_responder import (
    responder_ticket_segun_tipo,
    notificar_admin_con_contexto,
)

logger = logging.getLogger(__name__)
router = APIRouter()

VERIFY_TOKEN = os.getenv("META_WA_VERIFY_TOKEN", "")
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP_NOTIF", "")


def _obtener_sesion_cliente(whatsapp: str) -> Optional[dict]:
    """Obtiene sesión del cliente en BD."""
    try:
        resp = (
            admin()
            .table("whatsapp_sesiones")
            .select("*")
            .eq("whatsapp_numero", whatsapp)
            .order("fecha_creacion", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]
        return None
    except Exception as e:
        logger.warning(f"Error obteniendo sesión {whatsapp}: {e}")
        return None


def _crear_sesion_cliente(whatsapp: str, rol: str = "guest") -> dict:
    """Crea nueva sesión."""
    try:
        sesion = {
            "whatsapp_numero": whatsapp,
            "rol": rol,
            "estado": "activa",
            "mensaje_inicial": False,
            "fecha_creacion": datetime.now(timezone.utc).isoformat(),
        }
        result = admin().table("whatsapp_sesiones").insert(sesion).execute()
        if result.data:
            return result.data[0]
        return sesion
    except Exception as e:
        logger.warning(f"Error creando sesión {whatsapp}: {e}")
        return {"whatsapp_numero": whatsapp, "rol": rol}


def _save_message(whatsapp: str, role: str, content: str):
    """Guarda mensaje en historial."""
    try:
        admin().table("whatsapp_mensajes").insert({
            "whatsapp_numero": whatsapp,
            "rol": role,
            "contenido": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        pass


async def _crear_ticket_y_notificar(
    whatsapp: str,
    nombre: str,
    tipo: str,
    descripcion: str,
):
    """Crea ticket y notifica admin."""
    try:
        ticket_data = {
            "whatsapp_numero": whatsapp,
            "nombre": nombre,
            "tipo_solicitud": tipo,
            "descripcion": descripcion,
            "estado": "abierto",
            "fecha_creacion": datetime.now(timezone.utc).isoformat(),
        }
        
        result = admin().table("tickets").insert(ticket_data).execute()
        if not result.data:
            return
        
        ticket_id = result.data[0].get("id")
        
        # Auto-responder
        respuesta_info = await responder_ticket_segun_tipo(ticket_id, ticket_data)
        
        # Notificar admin
        if ADMIN_WHATSAPP:
            await notificar_admin_con_contexto(
                ticket_id=ticket_id,
                ticket_data=ticket_data,
                respuesta_info=respuesta_info,
                admin_whatsapp=ADMIN_WHATSAPP,
            )
        
        logger.info(f"Ticket #{ticket_id} creado")
        
    except Exception as e:
        logger.error(f"Error creando ticket: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/whatsapp/webhook")
async def webhook_whatsapp(request: Request):
    """Recibe mensajes de Meta WhatsApp."""
    body_bytes = await request.body()
    signature_header = request.headers.get("x-hub-signature-256", "")
    
    if not verify_signature(body_bytes, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    if data.get("entry"):
        for entry in data["entry"]:
            if entry.get("changes"):
                for change in entry["changes"]:
                    if change.get("value", {}).get("messages"):
                        await _procesar_mensaje(change["value"])
    
    return {"status": "ok"}


async def _procesar_mensaje(webhook_data: dict):
    """Procesa un mensaje recibido."""
    try:
        whatsapp_num = webhook_data.get("contacts", [{}])[0].get("wa_id", "")
        nombre_cliente = webhook_data.get("contacts", [{}])[0].get("profile", {}).get("name", "Cliente")
        
        if not whatsapp_num:
            return
        
        messages = webhook_data.get("messages", [])
        if not messages:
            return
        
        mensaje_texto = messages[0].get("text", {}).get("body", "").strip()
        if not mensaje_texto:
            return
        
        logger.info(f"Mensaje de {whatsapp_num}: {mensaje_texto[:50]}")
        
        # ✅ OBTENER O CREAR SESIÓN
        sesion = _obtener_sesion_cliente(whatsapp_num)
        
        # ✅ SI NO EXISTE SESIÓN = PRIMER MENSAJE → ENVIAR SALUDO Y SALIR
        if not sesion:
            _crear_sesion_cliente(whatsapp_num)
            await send_text_message(
                whatsapp_num,
                "🌱 ¡Hola! Bienvenido a ViveroOnline.com.co\n\n¿Qué necesitás hoy?\n1️⃣ Comprar plantas\n2️⃣ Vender mis plantas\n3️⃣ Consultar"
            )
            return  # ✅ SALIR AQUÍ, NO PROCESAR MÁS
        
        # ✅ SESIÓN EXISTE = PROCESAR COMANDO
        lower = mensaje_texto.lower()
        
        # ── PRECIO PARA COMPRADOR ──────────────────────────────────────────
        if lower.startswith("precio "):
            producto_nombre = lower.replace("precio ", "", 1).strip()
            if producto_nombre:
                try:
                    respuesta = await procesar_consulta_precio_producto(
                        supabase=admin(),
                        producto_nombre=producto_nombre,
                        es_guest=False,
                        plazo="inmediato",
                    )
                    _save_message(whatsapp_num, "bot", respuesta)
                    await send_text_message(whatsapp_num, respuesta)
                    return
                except Exception as e:
                    logger.error(f"Error precio: {e}")
                    await send_text_message(whatsapp_num, "⚠️ Error al consultar precio.")
                    return
        
        # ── COMANDOS ───────────────────────────────────────────────────────
        if mensaje_texto in ("1", "1️⃣"):
            await send_text_message(whatsapp_num, "📍 Explora el catálogo:\nhttps://app.viveroonline.com.co/marketplace")
            return
        
        if mensaje_texto in ("2", "2️⃣"):
            await send_text_message(whatsapp_num, "🌳 Registrate:\nhttps://app.viveroonline.com.co/registro-vivero")
            return
        
        if mensaje_texto in ("3", "3️⃣"):
            await send_text_message(whatsapp_num, "❓ Escribe tu consulta o escribí 'ayuda'")
            return
        
        if lower in ("salir", "exit", "fin"):
            await send_text_message(whatsapp_num, "Sesión cerrada. ¡Hasta pronto! 🌿")
            return
        
        # ── TICKET POR DEFECTO ─────────────────────────────────────────────
        await _crear_ticket_y_notificar(
            whatsapp=whatsapp_num,
            nombre=nombre_cliente,
            tipo="consulta",
            descripcion=mensaje_texto,
        )
        
        await send_text_message(
            whatsapp_num,
            f"✅ ¡Recibí tu solicitud!\n\nNuestro equipo te contactará en las próximas horas."
        )
        
    except Exception as e:
        logger.exception(f"Error procesando: {e}")


@router.get("/api/whatsapp/webhook")
async def verify_whatsapp_webhook(
    hub_mode: str = "",
    hub_challenge: str = "",
    hub_verify_token: str = "",
):
    """Verifica webhook con Meta."""
    if hub_verify_token == VERIFY_TOKEN:
        logger.info("WhatsApp webhook verificado")
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    
    raise HTTPException(status_code=403, detail="Invalid token")
