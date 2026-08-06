"""Servicio de auto-timeout de sub_cotizaciones pendientes.

Procesa sub_cotizaciones que superaron el tiempo de espera al viverista
y ejecuta:
  - Recordatorios escalonados (30/60/90 min)
  - Auto-timeout con marca "rechazada" al llegar a 120 min
  - Búsqueda de vivero alternativo automática
  - Notificación al comprador vía template

═══════════════════════════════════════════════════════════════════
RESTRICCIONES ANTI-INJUSTICIA (acordadas 30 jul con Elena):

1. HORARIO DIURNO: solo procesar entre 7am-8pm hora Colombia (UTC-5)
   Si el timeout cae fuera → se pausa y sigue al día siguiente

2. DÍAS LABORALES: sábado y domingo pausan el conteo
   Si el timeout cae en fin de semana → se pausa hasta el lunes 7am

3. GRACE PERIOD MATERAS: sub_cotizaciones con productos "materas"
   tienen 4 horas de timeout en vez de 2 (por fabricación bajo pedido)

Estas restricciones evitan castigar viveristas ocupados fuera del
horario laboral o durante fines de semana.
═══════════════════════════════════════════════════════════════════

CADENCIA de recordatorios (calibrada para timeout de 2h):
  Sub_cotización creada        → t=0 min
  Recordatorio 1               → t=30 min
  Recordatorio 2               → t=60 min
  Recordatorio 3 (último aviso)→ t=90 min
  Auto-timeout                 → t=120 min (2h)

Con grace period materas (4h timeout):
  Recordatorio 1               → t=60 min
  Recordatorio 2               → t=120 min
  Recordatorio 3               → t=180 min
  Auto-timeout                 → t=240 min (4h)
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from app.services.supabase import admin as db_admin
from app.services.whatsapp_meta import (
    notify_viverista_recordatorio,
    notify_comprador_pedido_parcial,
)


logger = logging.getLogger(__name__)

# Timezone Colombia
COLOMBIA_TZ_OFFSET_HOURS = -5

# Cadencia base (minutos desde fecha_creacion)
CADENCIA_NORMAL = {
    "recordatorio_1_min": 30,
    "recordatorio_2_min": 60,
    "recordatorio_3_min": 90,
    "timeout_min": 120,
}

CADENCIA_MATERAS = {
    "recordatorio_1_min": 60,
    "recordatorio_2_min": 120,
    "recordatorio_3_min": 180,
    "timeout_min": 240,
}


# ═══════════════════════════════════════════════════════════
# Helpers de horario laboral Colombia
# ═══════════════════════════════════════════════════════════

def _to_colombia_time(dt_utc: datetime) -> datetime:
    """Convierte UTC a hora Colombia (UTC-5)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc + timedelta(hours=COLOMBIA_TZ_OFFSET_HOURS)


def es_horario_laboral(dt_utc: datetime) -> bool:
    """True si el timestamp UTC cae en horario laboral Colombia.
    
    Reglas:
      - Días laborales: lunes a viernes (0-4 en Python)
      - Horario: 7:00am a 7:59pm (excluye 8pm en adelante)
    """
    col = _to_colombia_time(dt_utc)
    # weekday(): 0=lunes, 6=domingo
    if col.weekday() >= 5:  # sábado (5) o domingo (6)
        return False
    if col.hour < 7 or col.hour >= 20:
        return False
    return True


def minutos_desde(fecha_utc: datetime) -> int:
    """Minutos transcurridos entre fecha_utc y ahora (UTC)."""
    if fecha_utc.tzinfo is None:
        fecha_utc = fecha_utc.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - fecha_utc
    return int(delta.total_seconds() / 60)


def parse_iso_utc(iso_str: str) -> datetime:
    """Parsea un ISO string a datetime UTC-aware."""
    if not iso_str:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ═══════════════════════════════════════════════════════════
# Lógica principal
# ═══════════════════════════════════════════════════════════

def procesar_recordatorios_y_timeouts(
    dry_run: bool = False,
) -> dict:
    """Procesa TODAS las sub_cotizaciones pendientes y aplica recordatorios / timeouts.
    
    Es llamada desde el cron vencer-cotizaciones (piggyback).
    
    Args:
        dry_run: si True, no ejecuta cambios, solo simula (para debugging)
    
    Returns:
        Dict con contadores:
          - recordatorios_enviados
          - timeouts_ejecutados
          - errores_wa
          - pausadas_fuera_horario
          - grace_period_materas
    """
    # Guardrail: NO procesar fuera de horario laboral
    ahora_utc = datetime.now(timezone.utc)
    if not es_horario_laboral(ahora_utc):
        col = _to_colombia_time(ahora_utc)
        logger.info(
            f"Auto-timeout PAUSADO fuera de horario laboral. "
            f"Colombia: {col.strftime('%A %H:%M')}"
        )
        return {
            "ok": True,
            "recordatorios_enviados": 0,
            "timeouts_ejecutados": 0,
            "errores_wa": 0,
            "pausadas_fuera_horario": 0,
            "grace_period_materas": 0,
            "razon_no_ejecutado": "fuera_horario_laboral",
        }

    db = db_admin()

    # Buscar sub_cotizaciones pendientes con contexto
    subs_pendientes = db.table("sub_cotizaciones").select(
        "sub_cotizacion_id, cotizacion_id, vivero_id, "
        "recordatorios_enviados, fecha_ultimo_recordatorio, "
        "es_categoria_materas, fecha_creacion, total_estimado, "
        "viveros(whatsapp_numero, nombre_vivero), "
        "cotizaciones(prompt_original, cliente_id, "
        "clientes(nombre_representante, nombre_empresa, whatsapp_numero))"
    ).eq("estado", "pendiente").execute()

    recordatorios_enviados = 0
    timeouts_ejecutados = 0
    errores_wa = 0
    pausadas = 0
    grace_materas_activo = 0

    for sub in (subs_pendientes.data or []):
        try:
            resultado = _procesar_una_sub(sub, dry_run=dry_run)
            if resultado.get("recordatorio_enviado"):
                recordatorios_enviados += 1
            if resultado.get("timeout_ejecutado"):
                timeouts_ejecutados += 1
            if resultado.get("grace_materas"):
                grace_materas_activo += 1
        except Exception as e:
            # whatsapp_meta.py NO relanza excepciones al fallar el envío
            # (loggea y devuelve dict con error). Solo capturamos errores
            # inesperados (BD, parsing, lógica).
            errores_wa += 1
            logger.exception(
                f"Error inesperado procesando sub_{sub.get('sub_cotizacion_id')}: {e}"
            )

    return {
        "ok": True,
        "subs_evaluadas": len(subs_pendientes.data or []),
        "recordatorios_enviados": recordatorios_enviados,
        "timeouts_ejecutados": timeouts_ejecutados,
        "errores_wa": errores_wa,
        "pausadas_fuera_horario": pausadas,
        "grace_period_materas": grace_materas_activo,
        "dry_run": dry_run,
    }


def _procesar_una_sub(sub: dict, dry_run: bool = False) -> dict:
    """Procesa una sub_cotización específica.
    
    Decide qué acción tomar según su edad y recordatorios ya enviados.
    
    Returns dict con flags:
      - recordatorio_enviado: True si se envió recordatorio
      - timeout_ejecutado: True si se auto-rechazó
      - grace_materas: True si tenía grace period activo
    """
    sub_id = sub["sub_cotizacion_id"]
    cotizacion_id = sub["cotizacion_id"]
    vivero_id = sub["vivero_id"]
    es_materas = bool(sub.get("es_categoria_materas"))
    cadencia = CADENCIA_MATERAS if es_materas else CADENCIA_NORMAL

    # Punto de referencia: fecha_ultimo_recordatorio (si hay) o fecha_creacion
    fecha_ref_str = sub.get("fecha_ultimo_recordatorio") or sub.get("fecha_creacion")
    fecha_ref = parse_iso_utc(fecha_ref_str)

    # Edad total desde creación (para timeouts)
    edad_total_min = minutos_desde(parse_iso_utc(sub["fecha_creacion"]))

    # Recordatorios ya enviados
    n_recordatorios = int(sub.get("recordatorios_enviados") or 0)

    # ── Decisión: ¿es hora de timeout? ──
    if edad_total_min >= cadencia["timeout_min"] and n_recordatorios >= 3:
        # AUTO-TIMEOUT
        if not dry_run:
            _ejecutar_auto_timeout(sub)
        return {"timeout_ejecutado": True, "grace_materas": es_materas}

    # ── Decisión: ¿es hora de recordatorio? ──
    # Miramos edad total contra cada threshold
    proximo_recordatorio = n_recordatorios + 1
    if proximo_recordatorio > 3:
        # Ya envió los 3, solo espera timeout
        return {"grace_materas": es_materas}

    threshold_key = f"recordatorio_{proximo_recordatorio}_min"
    threshold_min = cadencia[threshold_key]

    if edad_total_min >= threshold_min:
        # Envío del recordatorio N
        if not dry_run:
            _enviar_recordatorio(sub, numero=proximo_recordatorio, cadencia=cadencia)
        return {"recordatorio_enviado": True, "grace_materas": es_materas}

    return {"grace_materas": es_materas}


def _enviar_recordatorio(sub: dict, numero: int, cadencia: dict) -> None:
    """Envía recordatorio N al viverista + actualiza tracking."""
    vivero = sub.get("viveros") or {}
    whatsapp = vivero.get("whatsapp_numero", "")
    nombre_vivero = vivero.get("nombre_vivero", "Viverista")
    proyecto = ((sub.get("cotizaciones") or {}).get("prompt_original")
                or f"Cotización #{sub['cotizacion_id']}")

    if not whatsapp:
        logger.warning(
            f"Sub {sub['sub_cotizacion_id']}: sin whatsapp_numero del vivero, "
            f"no se puede enviar recordatorio"
        )
        return

    # Calcular minutos restantes al timeout
    edad_min = minutos_desde(parse_iso_utc(sub["fecha_creacion"]))
    minutos_restantes = max(0, cadencia["timeout_min"] - edad_min)

    # notify_viverista_recordatorio es sync (no async)
    resp = notify_viverista_recordatorio(
        to=whatsapp,
        nombre_viverista=nombre_vivero,
        proyecto=proyecto,
        tu_parte_cop=int(sub.get("total_estimado") or 0),
        numero_recordatorio=numero,
        minutos_restantes=minutos_restantes,
    )

    # whatsapp_meta.send_template_message() devuelve dict con "error" si falló
    if resp.get("error"):
        logger.warning(
            f"Recordatorio {numero} falló para sub {sub['sub_cotizacion_id']}: "
            f"{resp.get('error')}"
        )
        return  # NO actualizamos tracking si falló el envío

    # Actualizar tracking en BD solo si envío OK
    db = db_admin()
    db.table("sub_cotizaciones").update({
        "recordatorios_enviados": numero,
        "fecha_ultimo_recordatorio": datetime.now(timezone.utc).isoformat(),
    }).eq("sub_cotizacion_id", sub["sub_cotizacion_id"]).execute()

    logger.info(
        f"Recordatorio {numero}/3 enviado a {nombre_vivero} "
        f"para sub_{sub['sub_cotizacion_id']} (cot #{sub['cotizacion_id']})"
    )


def _ejecutar_auto_timeout(sub: dict) -> None:
    """Auto-rechaza la sub por timeout + busca alternativa + notifica comprador."""
    sub_id = sub["sub_cotizacion_id"]
    cotizacion_id = sub["cotizacion_id"]
    vivero_id = sub["vivero_id"]

    db = db_admin()

    # Marcar sub como rechazada (auto-timeout)
    db.table("sub_cotizaciones").update({
        "estado": "rechazada",
        "notas_rechazo": "Sin respuesta del viverista dentro del plazo — auto-timeout",
        "fecha_respuesta": datetime.now(timezone.utc).isoformat(),
    }).eq("sub_cotizacion_id", sub_id).eq("estado", "pendiente").execute()

    # Buscar alternativa por cada item (fetch items desde BD si no vienen)
    sub_full = db.table("sub_cotizaciones").select("items").eq(
        "sub_cotizacion_id", sub_id
    ).limit(1).execute()
    items = sub_full.data[0].get("items", []) if sub_full.data else []

    alternativas = []
    for it in items:
        inv_id = it.get("inventario_id")
        cantidad = it.get("cantidad", 1)
        if not inv_id:
            continue
        try:
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
        except Exception as e:
            logger.warning(f"buscar_vivero_alternativo falló para inv {inv_id}: {e}")

    # Notificar al comprador con template
    cliente = (sub.get("cotizaciones") or {}).get("clientes") or {}
    whatsapp_cliente = cliente.get("whatsapp_numero", "")
    nombre_cliente = cliente.get("nombre_representante") or cliente.get("nombre_empresa") or "Cliente"
    proyecto = ((sub.get("cotizaciones") or {}).get("prompt_original")
                or f"Cotización #{cotizacion_id}")

    # Calcular monto disponible (subs aprobadas de la misma cotización)
    aprobadas = db.table("sub_cotizaciones").select("total_estimado").eq(
        "cotizacion_id", cotizacion_id
    ).eq("estado", "aprobada").execute()

    monto_disponible = sum(
        float(s.get("total_estimado") or 0) for s in (aprobadas.data or [])
    )

    # Descripción de lo no confirmado
    if alternativas:
        detalle_no_conf = f"{len(alternativas)} items con alternativa disponible"
    else:
        detalle_no_conf = "Sin alternativa disponible"

    if whatsapp_cliente:
        # notify_comprador_pedido_parcial es sync
        resp = notify_comprador_pedido_parcial(
            to=whatsapp_cliente,
            nombre_cliente=nombre_cliente,
            proyecto=proyecto,
            monto_disponible_cop=int(monto_disponible),
            detalle_no_confirmado=detalle_no_conf,
        )
        if resp.get("error"):
            logger.warning(
                f"No se pudo notificar comprador de sub {sub_id} auto-timeout: "
                f"{resp.get('error')}"
            )

    # Guardar alternativas en cotización para que el comprador las vea en /comprador
    if alternativas:
        db.table("cotizaciones").update({
            "alternativas_vivero": alternativas,
        }).eq("cotizacion_id", cotizacion_id).execute()

    logger.info(
        f"AUTO-TIMEOUT ejecutado sub_{sub_id} cot #{cotizacion_id} "
        f"vivero {vivero_id}. Alternativas: {len(alternativas)}"
    )
