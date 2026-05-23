"""Endpoints de ingesta de datos externos para Predicción de Demanda.

Sprint 1 — Solo SECOP II por trigger manual (admin-only).
Sprint 3 agregará Vercel Cron para automatizar.

POST /api/ingesta/secop          → query SECOP API + upsert + clasificación IA
GET  /api/ingesta/secop/procesos → lista procesos en BD + clasificación + resumen
"""
from __future__ import annotations
import logging
from collections import Counter
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


# ─────────────────── SCHEMAS - POST /secop ───────────────────

class IngestaSecopRequest(BaseModel):
    dias_atras: int = Field(default=90, ge=1, le=365)
    limit: int = Field(default=200, ge=1, le=2000)
    municipios: Optional[list[str]] = None
    keywords: Optional[list[str]] = None
    clasificar: bool = True


class IngestaSecopResponse(BaseModel):
    ok: bool
    ingestados: int
    nuevos: int
    clasificados: int
    relevantes: int
    fecha_desde: str
    keywords_count: int
    municipios_count: int
    warnings: list[str] = Field(default_factory=list)


# ─────────────────── SCHEMAS - GET /secop/procesos ───────────────────

class ClasificacionPreview(BaseModel):
    """Análisis de IA de un proceso (si fue clasificado)."""
    especies_mencionadas: list[str] = Field(default_factory=list)
    cantidad_estimada: Optional[int] = None
    altura_estimada_cm: Optional[int] = None
    tipo_proyecto: Optional[str] = None
    fecha_estimada_entrega: Optional[str] = None
    es_relevante: bool = False
    confianza: float = 0.0


class ProcesoSecopOut(BaseModel):
    """Proceso de SECOP listo para mostrar en UI."""
    proceso_id: str
    entidad: Optional[str] = None
    ciudad: Optional[str] = None
    departamento: Optional[str] = None
    objeto: Optional[str] = None
    cuantia_proceso: Optional[float] = None
    fecha_publicacion: Optional[str] = None
    estado_proceso: Optional[str] = None
    modalidad_contratacion: Optional[str] = None
    url_proceso: Optional[str] = None
    clasificacion: Optional[ClasificacionPreview] = None


class EspecieResumen(BaseModel):
    especie: str
    menciones: int


class ProcesosListResponse(BaseModel):
    total: int
    procesos: list[ProcesoSecopOut]
    resumen_especies: list[EspecieResumen]
    total_clasificados: int
    total_relevantes: int


# ─────────────────── ENDPOINT - POST /secop ───────────────────

@router.post("/secop", response_model=IngestaSecopResponse)
async def ingestar_secop(
    req: IngestaSecopRequest,
    user: UserContext = Depends(require_user),
):
    """Trigger manual de ingesta de SECOP II. Admin-only."""
    if user.rol != "admin":
        raise HTTPException(403, detail="Solo admins pueden disparar la ingesta de SECOP")

    fecha_desde = (date.today() - timedelta(days=req.dias_atras)).isoformat()
    keywords = req.keywords or KEYWORDS_PLANTAS
    municipios = req.municipios or MUNICIPIOS_PRIORITARIOS
    warnings: list[str] = []

    db = admin()

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
        raise HTTPException(502, detail=f"Error queriando SECOP: {str(e)[:300]}")

    if not raw_procesos:
        return IngestaSecopResponse(
            ok=True,
            ingestados=0, nuevos=0, clasificados=0, relevantes=0,
            fecha_desde=fecha_desde,
            keywords_count=len(keywords),
            municipios_count=len(municipios),
            warnings=["SECOP no devolvió procesos para esos filtros"],
        )

    nuevos = 0
    procesos_para_clasificar: list[dict] = []

    for raw in raw_procesos:
        try:
            campos = extraer_campos(raw)
            proceso_id = campos.get("proceso_id")
            if not proceso_id:
                warnings.append(f"Row sin proceso_id descartada (keys: {list(raw.keys())[:5]})")
                continue

            existing = db.table("secop_procesos").select("proceso_id").eq(
                "proceso_id", proceso_id
            ).limit(1).execute()
            is_new = not existing.data

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
                    f"Clasificación falló para {proceso.get('proceso_id')}: {str(e)[:100]}"
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
        warnings=warnings[:50],
    )


# ─────────────────── ENDPOINT - GET /secop/procesos ───────────────────

@router.get("/secop/procesos", response_model=ProcesosListResponse)
async def listar_procesos_secop(
    limit: int = 100,
    offset: int = 0,
    relevantes_only: bool = False,
    user: UserContext = Depends(require_user),
):
    """Lista procesos SECOP guardados en BD con su clasificación IA.

    Devuelve también un resumen agregado de especies mencionadas
    (útil para el panel de "Plantas necesitadas" en /admin).

    Admin-only.
    """
    if user.rol != "admin":
        raise HTTPException(403, detail="Solo admins")

    db = admin()

    # 1) Procesos paginados, ordenados por fecha desc
    procesos_result = (
        db.table("secop_procesos")
        .select("*")
        .order("fecha_publicacion", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    procesos_raw = procesos_result.data or []

    # 2) Clasificaciones de esos procesos
    proceso_ids = [p["proceso_id"] for p in procesos_raw]
    clasif_by_id: dict[str, dict] = {}
    if proceso_ids:
        clasif_result = (
            db.table("secop_clasificacion")
            .select("*")
            .in_("proceso_id", proceso_ids)
            .execute()
        )
        clasif_by_id = {c["proceso_id"]: c for c in (clasif_result.data or [])}

    # 3) Totales globales (separados para que sean exactos)
    total_result = db.table("secop_procesos").select("proceso_id", count="exact").execute()
    total_general = total_result.count or 0

    total_clasificados_result = (
        db.table("secop_clasificacion").select("proceso_id", count="exact").execute()
    )
    total_clasificados = total_clasificados_result.count or 0

    total_relevantes_result = (
        db.table("secop_clasificacion")
        .select("proceso_id", count="exact")
        .eq("es_relevante", True)
        .execute()
    )
    total_relevantes = total_relevantes_result.count or 0

    # 4) Resumen agregado de especies (de TODAS las clasificaciones, no solo la página)
    all_clasif_result = (
        db.table("secop_clasificacion")
        .select("especies_mencionadas")
        .execute()
    )
    especies_flat: list[str] = []
    for row in all_clasif_result.data or []:
        especies = row.get("especies_mencionadas") or []
        if isinstance(especies, list):
            especies_flat.extend(str(e).strip().lower() for e in especies if e and str(e).strip())

    counts = Counter(especies_flat)
    resumen_especies = [
        EspecieResumen(especie=e, menciones=c)
        for e, c in counts.most_common(20)
    ]

    # 5) Armar procesos de salida con clasificación embedida
    procesos_out: list[ProcesoSecopOut] = []
    for p in procesos_raw:
        c = clasif_by_id.get(p["proceso_id"])
        clasificacion = None
        if c:
            clasificacion = ClasificacionPreview(
                especies_mencionadas=c.get("especies_mencionadas") or [],
                cantidad_estimada=c.get("cantidad_estimada"),
                altura_estimada_cm=c.get("altura_estimada_cm"),
                tipo_proyecto=c.get("tipo_proyecto"),
                fecha_estimada_entrega=c.get("fecha_estimada_entrega"),
                es_relevante=bool(c.get("es_relevante")),
                confianza=float(c.get("confianza") or 0.0),
            )

        if relevantes_only and (not clasificacion or not clasificacion.es_relevante):
            continue

        # Normalizar fecha_publicacion a string YYYY-MM-DD (puede venir como date)
        fp = p.get("fecha_publicacion")
        if fp is not None and not isinstance(fp, str):
            fp = str(fp)

        procesos_out.append(ProcesoSecopOut(
            proceso_id=p["proceso_id"],
            entidad=p.get("entidad"),
            ciudad=p.get("ciudad"),
            departamento=p.get("departamento"),
            objeto=p.get("objeto"),
            cuantia_proceso=p.get("cuantia_proceso"),
            fecha_publicacion=fp,
            estado_proceso=p.get("estado_proceso"),
            modalidad_contratacion=p.get("modalidad_contratacion"),
            url_proceso=p.get("url_proceso"),
            clasificacion=clasificacion,
        ))

    return ProcesosListResponse(
        total=total_general,
        procesos=procesos_out,
        resumen_especies=resumen_especies,
        total_clasificados=total_clasificados,
        total_relevantes=total_relevantes,
    )
