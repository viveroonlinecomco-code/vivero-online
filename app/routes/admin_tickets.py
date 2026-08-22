"""
Endpoints admin para gestión de tickets - ViveroOnline
Columnas reales: ticket_id, whatsapp_numero, nombre, tipo_solicitud,
descripcion, prioridad, estado, cliente_id, atendido_por (uuid),
fecha_creacion, fecha_atencion, notas_admin
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.services.supabase import admin as db_admin
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


def normalizar_whatsapp(numero: str) -> str:
    """Asegura que el número tenga + al inicio para WhatsApp API"""
    if not numero:
        return ""
    numero = numero.strip()
    if not numero.startswith("+"):
        numero = "+" + numero
    return numero


@router.get("/tickets/pendientes")
async def listar_tickets_pendientes():
    try:
        db = db_admin()
        resp = (
            db.table("tickets_soporte")
            .select("ticket_id, nombre, whatsapp_numero, tipo_solicitud, descripcion, prioridad, estado, fecha_creacion")
            .eq("estado", "pendiente")
            .order("fecha_creacion", desc=True)
            .execute()
        )
        tickets = resp.data or []
        # Asegurar que nombre nunca sea null en el frontend
        for t in tickets:
            if not t.get("nombre"):
                t["nombre"] = t.get("whatsapp_numero", "Sin nombre")
        logger.info(f"🎫 tickets/pendientes - {len(tickets)} resultados")
        return JSONResponse({"total": len(tickets), "tickets": tickets})
    except Exception as e:
        logger.error(f"Error tickets pendientes: {e}")
        return JSONResponse({"total": 0, "tickets": [], "error": str(e)})


@router.get("/tickets/respondidos")
async def listar_tickets_respondidos():
    try:
        db = db_admin()
        resp = (
            db.table("tickets_soporte")
            .select("ticket_id, nombre, whatsapp_numero, descripcion, estado, fecha_atencion, notas_admin")
            .eq("estado", "respondido")
            .order("fecha_atencion", desc=True)
            .limit(50)
            .execute()
        )
        tickets = resp.data or []
        for t in tickets:
            if not t.get("nombre"):
                t["nombre"] = t.get("whatsapp_numero", "Sin nombre")
        return JSONResponse({"total": len(tickets), "tickets": tickets})
    except Exception as e:
        logger.error(f"Error tickets respondidos: {e}")
        return JSONResponse({"total": 0, "tickets": []})


@router.get("/ticket/{ticket_id}")
async def obtener_ticket(ticket_id: int):
    try:
        db = db_admin()
        resp = db.table("tickets_soporte").select("*").eq("ticket_id", ticket_id).single().execute()
        return JSONResponse(resp.data or {})
    except Exception as e:
        logger.error(f"Error obteniendo ticket {ticket_id}: {e}")
        return JSONResponse({})


@router.post("/ticket/{ticket_id}/responder-whatsapp")
async def responder_whatsapp(ticket_id: int, payload: dict):
    """Responde un ticket: guarda en BD e intenta enviar WhatsApp"""
    try:
        db = db_admin()
        mensaje = payload.get("mensaje", "").strip()

        if not mensaje:
            return JSONResponse({"status": "error", "error": "Mensaje vacío"}, status_code=400)

        # 1. Obtener ticket
        ticket_resp = db.table("tickets_soporte").select(
            "ticket_id, whatsapp_numero, nombre, estado"
        ).eq("ticket_id", ticket_id).single().execute()

        if not ticket_resp.data:
            return JSONResponse({"status": "error", "error": "Ticket no encontrado"}, status_code=404)

        ticket = ticket_resp.data
        cliente_whatsapp = normalizar_whatsapp(ticket.get("whatsapp_numero", ""))
        nombre_cliente = ticket.get("nombre") or cliente_whatsapp or "Cliente"

        # 2. Intentar enviar WhatsApp (no bloquear si falla)
        whatsapp_enviado = False
        try:
            from app.services.whatsapp_meta import send_text_message
            result = await send_text_message(cliente_whatsapp, mensaje)
            whatsapp_enviado = bool(result)
            logger.info(f"📤 WhatsApp {'✅ enviado' if whatsapp_enviado else '❌ FALLÓ'} a {cliente_whatsapp}")
        except Exception as wa_err:
            logger.error(f"WhatsApp error ticket #{ticket_id}: {wa_err}")

        # 3. Actualizar ticket en BD
        now = datetime.now(timezone.utc).isoformat()
        notas = f"💬 [{now}] RESPUESTA ADMIN:\n{mensaje}"
        if not whatsapp_enviado:
            notas += "\n⚠️ WhatsApp no enviado - revisar configuración"

        db.table("tickets_soporte").update({
            "estado": "respondido",
            "fecha_atencion": now,
            "notas_admin": notas,
        }).eq("ticket_id", ticket_id).execute()

        logger.info(f"✅ Ticket #{ticket_id} marcado como respondido")

        return JSONResponse({
            "status": "ok",
            "ticket_id": ticket_id,
            "cliente": nombre_cliente,
            "whatsapp_enviado": whatsapp_enviado,
            "mensaje": f"✅ Ticket #{ticket_id} respondido" + (" · WhatsApp enviado ✅" if whatsapp_enviado else " · WhatsApp NO enviado ⚠️"),
        })

    except Exception as e:
        logger.error(f"Error respondiendo ticket {ticket_id}: {e}")
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@router.post("/ticket/{ticket_id}/guardar-notas")
async def guardar_notas(ticket_id: int, payload: dict):
    try:
        db = db_admin()
        notas = payload.get("notas", "").strip()
        db.table("tickets_soporte").update(
            {"notas_admin": notas}
        ).eq("ticket_id", ticket_id).execute()
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logger.error(f"Error guardando notas {ticket_id}: {e}")
        return JSONResponse({"status": "error"})
