"""Recolección de datos fiscales del vivero por WhatsApp.

Se dispara automáticamente ANTES de procesar la primera aprobación real
de una cotización, si el vivero aún no tiene datos fiscales registrados
(datos_fiscales_vivero.mandato_aceptado = False o registro inexistente).

Flujo de conversación:
  1. ViveroOnline detecta que el vivero quiere aprobar su primera cotización real
  2. Pausa la aprobación y envía el Contrato de Mandato por WhatsApp
  3. El viverista responde SÍ → se registra aceptación → continúa la aprobación
  4. ViveroOnline recolecta datos fiscales en pasos sucesivos (tipo persona,
     documento, nombre, banco, cuenta) usando accion_pendiente como estado
  5. Cuando están completos, el estado pasa a "pending" para verificación
     manual del equipo ViveroOnline (especialmente titular_coincide)

AJUSTE (18 jun): no se dispara en el onboarding inicial ni en el primer
mensaje del viverista — solo cuando va a aprobar una cotización real por
primera vez, para no asustar con burocracia antes de que haya una venta.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from app.services.supabase import admin as db_admin
from app.services.whatsapp_meta import send_text_message

logger = logging.getLogger(__name__)

# Versión del contrato — si cambia el texto, incrementar para re-solicitar
# aceptación a viveristas que aceptaron versiones anteriores.
MANDATO_VERSION = "v1.0"

CONTRATO_MANDATO_WA = """📋 *Contrato de Mandato Comercial — ViveroOnline*

Antes de procesar tu primera venta, necesitamos que aceptes nuestro contrato de mandato.

*¿Qué significa esto?*
ViveroOnline recauda el pago del comprador *en tu nombre*. Vos sos el vendedor real; nosotros somos tu mandatario.

*Condiciones principales:*
• Tu precio ingresado = lo que recibís neto
• ViveroOnline retiene 20% de orquestación (logística + garantía + plataforma)
• Te transferimos tu pago en 48h tras confirmación de entrega
• Sos responsable de facturar al comprador si aplica (DIAN)

*Marco legal:* Art. 2142-2199 Código Civil · Ley 527/1999 (comercio electrónico) · Ley 1480/2011 (consumidor)

El texto completo está disponible en:
https://www.viveroonline.com.co/contrato-de-mandato-comercial/

Al responder *SÍ ACEPTO*, confirmás que leíste y aceptás el Contrato de Mandato, con la misma validez que una firma escrita (Ley 527 de 1999). Tu número de WhatsApp y la fecha quedan registrados como prueba.

¿Aceptás el Contrato de Mandato?
Respondé *SÍ ACEPTO* o *NO* para cancelar."""


PASOS_DATOS_FISCALES = [
    {
        "campo": "tipo_persona",
        "pregunta": (
            "Perfecto, ya quedó registrada tu aceptación. ✅\n\n"
            "Ahora necesito tus datos fiscales para transferirte los pagos.\n\n"
            "¿Sos persona natural o jurídica?\n"
            "Respondé *1* para Natural (CC)\n"
            "Respondé *2* para Jurídica (NIT)"
        ),
        "opciones": {"1": "natural", "2": "juridica"},
    },
    {
        "campo": "numero_documento",
        "pregunta_natural": "Enviame tu número de *Cédula de Ciudadanía* (solo números, sin puntos):",
        "pregunta_juridica": "Enviame el *NIT* de tu empresa (sin guión ni dígito de verificación):",
    },
    {
        "campo": "digito_verificacion",
        "pregunta": "¿Cuál es el *dígito de verificación* de tu NIT? (el número después del guión)",
        "solo_juridica": True,
    },
    {
        "campo": "nombre_completo",
        "pregunta_natural": "¿Cuál es tu *nombre completo* tal como aparece en tu cédula?",
        "pregunta_juridica": "¿Cuál es la *razón social* de tu empresa tal como está en el RUT?",
    },
    {
        "campo": "representante_legal",
        "pregunta": "¿Cuál es el *nombre del representante legal*?",
        "solo_juridica": True,
    },
    {
        "campo": "banco",
        "pregunta": (
            "¿En qué banco tenés la cuenta para recibir tus pagos?\n\n"
            "Ej: Bancolombia, Davivienda, Banco de Bogotá, Nequi, Daviplata, etc."
        ),
    },
    {
        "campo": "tipo_cuenta",
        "pregunta": (
            "¿Qué tipo de cuenta es?\n"
            "Respondé *1* para Ahorros\n"
            "Respondé *2* para Corriente"
        ),
        "opciones": {"1": "ahorros", "2": "corriente"},
    },
    {
        "campo": "numero_cuenta",
        "pregunta": "Enviame el *número de cuenta* completo (solo números):",
    },
    {
        "campo": "titular_cuenta",
        "pregunta": (
            "¿A nombre de quién está la cuenta?\n"
            "⚠️ *Importante:* el titular debe coincidir exactamente con el "
            "nombre o razón social que registraste. Si no coincide, no podemos "
            "transferirte los pagos."
        ),
    },
]


def vivero_necesita_datos_fiscales(vivero_id: int) -> bool:
    """Retorna True si el vivero no tiene mandato aceptado en la versión actual."""
    try:
        db = db_admin()
        resp = db.table("datos_fiscales_vivero").select(
            "mandato_aceptado, mandato_version"
        ).eq("vivero_id", vivero_id).limit(1).execute()

        if not resp.data:
            return True
        d = resp.data[0]
        # Re-solicitar si la versión del contrato cambió
        return not d.get("mandato_aceptado") or d.get("mandato_version") != MANDATO_VERSION
    except Exception as e:
        logger.warning(f"No se pudo verificar datos fiscales vivero {vivero_id}: {e}")
        return False  # En caso de error, no bloquear la operación


async def iniciar_flujo_mandato(vivero_id: int, whatsapp: str, sesion_id: int, cotizacion_id: int):
    """Envía el contrato de mandato por WhatsApp y guarda el estado en
    accion_pendiente para retomar cuando el viverista responda."""
    await send_text_message(whatsapp, CONTRATO_MANDATO_WA)

    accion = {
        "type": "recolectar_datos_fiscales",
        "confirmed": False,
        "paso": "mandato",  # primer paso: aceptación del contrato
        "tipo_persona": None,
        "datos_recolectados": {},
        "params": {
            "vivero_id": vivero_id,
            "cotizacion_id_pendiente": cotizacion_id,
            # Guardamos la cotización que quería aprobar para retomarla
            # automáticamente una vez completados los datos fiscales.
        }
    }

    try:
        db = db_admin()
        db.table("sesiones_agente").update({
            "accion_pendiente": accion,
        }).eq("sesion_id", sesion_id).execute()
    except Exception as e:
        logger.warning(f"No se pudo guardar accion_pendiente mandato: {e}")


async def procesar_respuesta_datos_fiscales(
    mensaje: str,
    accion: dict,
    vivero_id: int,
    whatsapp: str,
    sesion_id: int,
) -> dict:
    """Procesa cada respuesta del viverista en el flujo de recolección.

    Retorna:
        {"completado": True, "cotizacion_id": X} cuando termina el flujo
        {"completado": False, "accion": {...}} para continuar el flujo
        {"cancelado": True} si el viverista rechazó el mandato
    """
    db = db_admin()
    paso = accion.get("paso", "mandato")
    datos = accion.get("datos_recolectados") or {}
    tipo_persona = accion.get("tipo_persona")
    lower = mensaje.strip().lower()

    # ── Paso 1: aceptación del contrato ───────────────────────────────────────
    if paso == "mandato":
        if lower in ("sí acepto", "si acepto", "sí", "si", "acepto", "1"):
            # Registrar aceptación del mandato
            fecha_aceptacion = datetime.now(timezone.utc)
            try:
                existing = db.table("datos_fiscales_vivero").select(
                    "datos_id"
                ).eq("vivero_id", vivero_id).limit(1).execute()

                if existing.data:
                    db.table("datos_fiscales_vivero").update({
                        "mandato_aceptado": True,
                        "mandato_aceptado_fecha": fecha_aceptacion.isoformat(),
                        "mandato_whatsapp_numero": whatsapp,
                        "mandato_version": MANDATO_VERSION,
                        "fecha_actualizacion": fecha_aceptacion.isoformat(),
                    }).eq("vivero_id", vivero_id).execute()
                else:
                    db.table("datos_fiscales_vivero").insert({
                        "vivero_id": vivero_id,
                        "mandato_aceptado": True,
                        "mandato_aceptado_fecha": fecha_aceptacion.isoformat(),
                        "mandato_whatsapp_numero": whatsapp,
                        "mandato_version": MANDATO_VERSION,
                    }).execute()
            except Exception as e:
                logger.error(f"No se pudo registrar aceptación mandato: {e}")

            # Avanzar al primer paso de datos fiscales
            primer_paso = PASOS_DATOS_FISCALES[0]
            await send_text_message(whatsapp, primer_paso["pregunta"])

            accion["paso"] = primer_paso["campo"]
            accion["confirmed"] = True
            return {"completado": False, "accion": accion}

        elif lower in ("no", "no acepto", "cancelar"):
            await send_text_message(
                whatsapp,
                "Entendido. Sin el Contrato de Mandato no podemos procesar pagos en tu nombre. "
                "Cuando quieras retomarlo, escribinos o intentá aprobar otra cotización.\n\n"
                "Si tenés preguntas sobre el contrato: viveroonline.com.co@gmail.com"
            )
            return {"cancelado": True}
        else:
            await send_text_message(
                whatsapp,
                "Para aceptar el contrato respondé *SÍ ACEPTO*, o *NO* para cancelar."
            )
            return {"completado": False, "accion": accion}

    # ── Pasos de recolección de datos fiscales ────────────────────────────────
    paso_actual = next((p for p in PASOS_DATOS_FISCALES if p["campo"] == paso), None)
    if not paso_actual:
        logger.warning(f"Paso desconocido en datos fiscales: {paso}")
        return {"completado": False, "accion": accion}

    # Validar y guardar respuesta del paso actual
    if "opciones" in paso_actual:
        valor = paso_actual["opciones"].get(mensaje.strip())
        if not valor:
            opciones_txt = " o ".join([f"*{k}*" for k in paso_actual["opciones"]])
            await send_text_message(whatsapp, f"Respondé {opciones_txt}:")
            return {"completado": False, "accion": accion}
        datos[paso] = valor
        if paso == "tipo_persona":
            accion["tipo_persona"] = valor
            tipo_persona = valor
    else:
        if len(mensaje.strip()) < 2:
            await send_text_message(whatsapp, "Por favor enviame la información completa.")
            return {"completado": False, "accion": accion}
        datos[paso] = mensaje.strip()

    accion["datos_recolectados"] = datos

    # Guardar progreso en BD
    try:
        db.table("datos_fiscales_vivero").update({
            paso: datos[paso],
            "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
        }).eq("vivero_id", vivero_id).execute()
    except Exception as e:
        logger.warning(f"No se pudo guardar campo {paso}: {e}")

    # Determinar siguiente paso
    idx_actual = next((i for i, p in enumerate(PASOS_DATOS_FISCALES) if p["campo"] == paso), -1)
    siguiente = None
    for p in PASOS_DATOS_FISCALES[idx_actual + 1:]:
        # Saltar pasos que no aplican según tipo de persona
        if p.get("solo_juridica") and tipo_persona != "juridica":
            continue
        siguiente = p
        break

    if siguiente:
        # Elegir pregunta según tipo de persona si hay variantes
        if "pregunta_natural" in siguiente and tipo_persona == "natural":
            pregunta = siguiente["pregunta_natural"]
        elif "pregunta_juridica" in siguiente and tipo_persona == "juridica":
            pregunta = siguiente["pregunta_juridica"]
        else:
            pregunta = siguiente.get("pregunta", "")

        await send_text_message(whatsapp, pregunta)
        accion["paso"] = siguiente["campo"]
        return {"completado": False, "accion": accion}

    # ── Todos los datos recolectados ──────────────────────────────────────────
    try:
        db.table("datos_fiscales_vivero").update({
            "estado_verificacion": "pending",
            "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
        }).eq("vivero_id", vivero_id).execute()
    except Exception as e:
        logger.warning(f"No se pudo actualizar estado_verificacion: {e}")

    nombre = datos.get("nombre_completo", "")
    banco = datos.get("banco", "")
    cuenta = datos.get("numero_cuenta", "")
    tipo_cta = datos.get("tipo_cuenta", "")

    await send_text_message(
        whatsapp,
        f"✅ *Datos recibidos — ViveroOnline*\n\n"
        f"Nombre: {nombre}\n"
        f"Banco: {banco} · {tipo_cta} · {cuenta}\n\n"
        f"Nuestro equipo verificará que el titular de la cuenta coincida con tu documento. "
        f"Te confirmamos en máximo 1 día hábil.\n\n"
        f"Mientras tanto, tu cotización quedó registrada y la procesamos en cuanto "
        f"se confirme tu cuenta. 🌿"
    )

    cotizacion_id = accion.get("params", {}).get("cotizacion_id_pendiente")
    return {"completado": True, "cotizacion_id": cotizacion_id}
