"""Endpoints públicos sin auth.

NOTA: El dashboard inversor antes vivía aquí (/api/public/flywheel) sin auth.
Ahora se movió a /api/inversores/flywheel y requiere cookie de invitación.
Solo dejamos health checks aquí.
"""
from fastapi import APIRouter


router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/health")
async def public_health():
    return {"ok": True, "service": "public-api", "ready": True}
