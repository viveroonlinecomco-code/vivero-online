"""Endpoints de ingesta de datos externos para Predicción de Demanda.

Sprint 1 — Solo SECOP II por trigger manual (admin-only).
Sprint 3 agregará Vercel Cron para automatizar.

POST /api/ingesta/secop  → query SECOP API + upsert + clasificación IA
"""
from __future__ import annotations
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.deps import UserContext, require_user
from app.services.secop import (
    SecopClient,
    KEYWORDS_PLANTAS,
    MUNICIPIOS_PRIORITARIOS,
    extraer_campos,
)
from app.agents.secop_classifier import SecopClassifier
from app.services.supabase import admin


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingesta", tags=["ingesta"])


# ─────────────────── SCHEMAS ───────────────────

class IngestaSecopRequest(BaseModel):
    """Parámetros opcionales para customizar la ingesta."""
    dias_atras: int = Field(
        default=90, ge=1, le=365,
        description="Cuántos días hacia atrás buscar procesos"
    )
    limit: int = Field(
        default=200, ge=1, le=2000,
        description="Máximo de procesos a ingestar en esta ejecución"
    )
    municipios: Optional[list[str]] = Field(
        default=None,
        description="Override de municipios (default: Sabana de Bogotá)"
    )
    keywords: Optional[list[str]] = Field(
        default=None,
        description="Override de keywords (default: lista de plantas/arborización)"
    )
    clasificar: bool = Field(
        default=True,
        description="Si correr Gemini para clasificar los procesos nuevos"
    )


class IngestaSecopResponse(BaseModel):
    """Resumen del resultado de la ingesta."""
    ok: bool
    ingestados: int           # cuántos vinieron de SECOP
    nuevos: int               # cuántos eran nuevos en la BD
    clasificados: int         # cuántos pasaron por Gemini
    relevantes: int           # cuántos eran realmente sobre plantas
    fecha_desde: str
    keywords_count: int
    municipios_count: int
    warnings: list[str] = Field(default_factory=list)


# ─────────────────── ENDPOINT ───────────────────

@router.post("/secop", response_model=IngestaSecopResponse)
async def ingestar_secop(
    req: IngestaSecopRequest,
    user: UserContext = Depends(require_user),
):
    """Trigger manual de ingesta de SECOP II.

    Pipeline:
    1. Query SECOP II API con filtros (keywords + municipios + fecha)
    2. Upsert en secop_procesos (dedup por proceso_id)
    3. Si clasificar=true: Gemini analiza cada proceso nuevo
    4. Upsert en secop_clasificacion + marca procesado_at en secop_procesos

    Solo accesible para usuarios admin.
    """
    # Defensive admin check (no asumimos que existe require_admin en deps.py)
    if user.rol != "admin":
        raise HTTPException(
            403,
            detail="Solo admins pueden disparar la ingesta de SECOP",
        )

    fecha_desde = (date.today() - timedelta(days=req.dias_atras)).isoformat()
    keywords = req.keywords or KEYWORDS_PLANTAS
    municipios = req.municipios or MUNICIPIOS_PRIORITARIOS
    warnings: list[str] = []

    db = admin()

    # ─── 1. Query SECOP API ───
    logger.info(
        f"SECOP ingesta: fecha_desde={fecha_desde}, "
        f"keywords={len(keywords)}, municipios={len(municipios)}, "
        f"limit={req.limit}"
    )
    try:
        async with SecopClient() as client:
            raw_procesos = await client.fetch_procesos(
                keywords=keywords,
                municipios=municipios,
                fecha_desde=fecha_desde,
                limit=req.limit,
            )
    except Exception as e:
        logger.error(f"SECOP API falló: {e}")
        raise HTTPException(
            502, detail=f"Error queriando SECOP: {str(e)[:300]}"
        )

    if not raw_procesos:
        return IngestaSecopResponse(
            ok=True,
            ingestados=0, nuevos=0, clasificados=0, relevantes=0,
            fecha_desde=fecha_desde,
            keywords_count=len(keywords),
            municipios_count=len(municipios),
            warnings=["SECOP no devolvió procesos para esos filtros"],
        )

    # ─── 2. Upsert en secop_procesos ───
    nuevos = 0
    procesos_para_clasificar: list[dict] = []

    for raw in raw_procesos:
        try:
            campos = extraer_campos(raw)
            proceso_id = campos.get("proceso_id")
            if not proceso_id:
                warnings.append(
                    f"Row sin proceso_id descartada "
                    f"(keys: {list(raw.keys())[:5]})"
                )
                continue

            # ¿Ya existe?
            existing = db.table("secop_procesos").select("proceso_id").eq(
                "proceso_id", proceso_id
            ).limit(1).execute()
            is_new = not existing.data

            # Upsert (insert si nuevo, update si existe)
            db.table("secop_procesos").upsert(
                campos,
                on_conflict="proceso_id",
            ).execute()

            if is_new:
                nuevos += 1
                procesos_para_clasificar.append(campos)
        except Exception as e:
            logger.warning(f"Error procesando row: {e}")
            warnings.append(f"Row falló: {str(e)[:100]}")
            continue

    # ─── 3. Clasificación con Gemini (opcional) ───
    clasificados = 0
    relevantes = 0

    if req.clasificar and procesos_para_clasificar:
        try:
            classifier = SecopClassifier()
        except Exception as e:
            logger.error(f"No se pudo instanciar SecopClassifier: {e}")
            warnings.append(f"Clasificador no inicializó: {str(e)[:200]}")
            return IngestaSecopResponse(
                ok=True,
                ingestados=len(raw_procesos),
                nuevos=nuevos,
                clasificados=0,
                relevantes=0,
                fecha_desde=fecha_desde,
                keywords_count=len(keywords),
                municipios_count=len(municipios),
                warnings=warnings,
            )

        now_iso = datetime.now(timezone.utc).isoformat()

        for proceso in procesos_para_clasificar:
            try:
                analisis, raw_response = classifier.classify(
                    objeto=proceso.get("objeto") or "",
                    entidad=proceso.get("entidad"),
                    ciudad=proceso.get("ciudad"),
                    cuantia=proceso.get("cuantia_proceso"),
                )

                # Upsert clasificación
                payload = {
                    "proceso_id": proceso["proceso_id"],
                    "especies_mencionadas": analisis.especies_mencionadas,
                    "cantidad_estimada": analisis.cantidad_estimada,
                    "altura_estimada_cm": analisis.altura_estimada_cm,
                    "tipo_proyecto": analisis.tipo_proyecto,
                    "fecha_estimada_entrega": analisis.fecha_estimada_entrega_iso,
                    "es_relevante": analisis.es_relevante,
                    "confianza": analisis.confianza,
                    "modelo_usado": "gemini-2.5-flash",
                    "raw_response": raw_response,
                }
                db.table("secop_clasificacion").upsert(
                    payload,
                    on_conflict="proceso_id",
                ).execute()

                # Marcar procesado_at en secop_procesos
                db.table("secop_procesos").update(
                    {"procesado_at": now_iso}
                ).eq("proceso_id", proceso["proceso_id"]).execute()

                clasificados += 1
                if analisis.es_relevante:
                    relevantes += 1
            except Exception as e:
                logger.warning(
                    f"Error clasificando proceso {proceso.get('proceso_id')}: {e}"
                )
                warnings.append(
                    f"Clasificación falló para {proceso.get('proceso_id')}: "
                    f"{str(e)[:100]}"
                )
                continue

    return IngestaSecopResponse(
        ok=True,
        ingestados=len(raw_procesos),
        nuevos=nuevos,
        clasificados=clasificados,
        relevantes=relevantes,
        fecha_desde=fecha_desde,
        keywords_count=len(keywords),
        municipios_count=len(municipios),
        warnings=warnings[:50],  # cap warnings list size
    )
