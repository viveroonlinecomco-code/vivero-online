"""Dashboard inversor protegido por código de invitación.

Flujo:
1. Inversor entra a /inversores → si no tiene cookie válida, ve form de login
2. POST /api/inversores/login con su código → valida en BD → emite cookie firmada (24h)
3. GET /api/inversores/flywheel requiere cookie → devuelve datos anonimizados
4. Admin genera códigos vía POST /api/inversores/codigos (requiere rol=admin)

Cookie: HMAC-SHA256(invitacion_id + expiry) firmada con SUPABASE_JWT_SECRET.
No usamos JWT estándar para no chocar con auth de Supabase.
"""
from __future__ import annotations
import hashlib
import hmac
import time
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, Request
from pydantic import BaseModel, Field

from app.auth.deps import UserContext, require_admin
from app.config import get_settings
from app.services.supabase import admin


router = APIRouter(prefix="/api/inversores", tags=["inversores"])

COOKIE_NAME = "vo_inv_session"
COOKIE_TTL_SEC = 24 * 3600  # 24 horas


# ─────────────────── COOKIE FIRMADA ───────────────────

def _sign_cookie(invitacion_id: int, expiry: int) -> str:
    """Genera cookie firmada: '{id}.{expiry}.{hmac}'."""
    s = get_settings()
    payload = f"{invitacion_id}.{expiry}"
    sig = hmac.new(
        s.supabase_jwt_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{sig}"


def _verify_cookie(cookie_value: str) -> Optional[int]:
    """Valida la cookie y retorna el invitacion_id si es válida."""
    if not cookie_value:
        return None
    try:
        parts = cookie_value.split(".")
        if len(parts) != 3:
            return None
        invitacion_id_str, expiry_str, sig = parts
        invitacion_id = int(invitacion_id_str)
        expiry = int(expiry_str)

        # Verificar expiración
        if expiry < int(time.time()):
            return None

        # Verificar firma (constant-time comparison)
        expected = _sign_cookie(invitacion_id, expiry).split(".")[2]
        if not hmac.compare_digest(sig, expected):
            return None

        return invitacion_id
    except (ValueError, IndexError):
        return None


def _require_inversor_cookie(
    vo_inv_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> int:
    """Dependencia que valida la cookie del inversor."""
    invitacion_id = _verify_cookie(vo_inv_session or "")
    if not invitacion_id:
        raise HTTPException(
            status_code=401,
            detail="Sesión inversor inválida o expirada. Vuelve a ingresar tu código.",
        )
    return invitacion_id


# ─────────────────── LOGIN INVERSOR ───────────────────

class LoginRequest(BaseModel):
    codigo: str = Field(..., min_length=4, max_length=40)


class LoginResponse(BaseModel):
    ok: bool
    nombre_inversor: str
    expira_en: int


@router.post("/login", response_model=LoginResponse)
async def login_inversor(req: LoginRequest, response: Response):
    """Valida código de invitación → emite cookie firmada."""
    db = admin()
    code = req.codigo.strip().upper()

    inv = db.table("invitaciones_inversor").select(
        "id, nombre_inversor, activo, expira_en, total_accesos, primer_uso_en"
    ).eq("codigo", code).limit(1).execute()

    if not inv.data:
        raise HTTPException(401, detail="Código de invitación no válido")

    invitacion = inv.data[0]

    if not invitacion.get("activo"):
        raise HTTPException(401, detail="Este código está desactivado")

    # Verificar expiración del código (no de la cookie)
    if invitacion.get("expira_en"):
        from datetime import datetime, timezone
        try:
            exp = datetime.fromisoformat(invitacion["expira_en"].replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                raise HTTPException(401, detail="Este código de invitación ha expirado")
        except (ValueError, AttributeError):
            pass

    # Trackear uso
    update = {
        "ultimo_uso_en": "now()",
        "total_accesos": (invitacion.get("total_accesos") or 0) + 1,
    }
    if not invitacion.get("primer_uso_en"):
        update["primer_uso_en"] = "now()"
    db.table("invitaciones_inversor").update(update).eq("id", invitacion["id"]).execute()

    # Emitir cookie
    expiry = int(time.time()) + COOKIE_TTL_SEC
    cookie_value = _sign_cookie(invitacion["id"], expiry)

    s = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_value,
        max_age=COOKIE_TTL_SEC,
        httponly=True,
        secure=s.is_production,
        samesite="lax",
        path="/",
    )

    return LoginResponse(
        ok=True,
        nombre_inversor=invitacion["nombre_inversor"],
        expira_en=expiry,
    )


@router.post("/logout")
async def logout_inversor(response: Response):
    """Borra la cookie del inversor."""
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


# ─────────────────── DASHBOARD PROTEGIDO ───────────────────

@router.get("/flywheel")
async def get_flywheel_data(invitacion_id: int = Depends(_require_inversor_cookie)):
    """KPIs anonimizados - requiere cookie de inversor válida."""
    db = admin()

    k_resp = db.table("v_public_flywheel").select("*").execute()
    c_resp = db.table("v_public_crecimiento").select("*").execute()
    m_resp = db.table("v_public_demanda_municipio").select("*").execute()
    e_resp = db.table("v_public_top_especies").select("*").limit(10).execute()

    k = (k_resp.data or [{}])[0]

    return {
        "ok": True,
        "version": "1.0",
        "kpis": {
            "gmv_aprox_cop": int(k.get("gmv_aprox_cop", 0) or 0),
            "transacciones_validas": k.get("transacciones_validas", 0),
            "compradores_activos": k.get("compradores_activos", 0),
            "viveristas_activos": k.get("viveristas_activos", 0),
            "viveros_registrados": k.get("viveros_registrados", 0),
            "items_disponibles": k.get("items_disponibles", 0),
            "txn_ultimas_24h": k.get("txn_ultimas_24h", 0),
        },
        "crecimiento_semanal": c_resp.data or [],
        "demanda_municipio": m_resp.data or [],
        "top_especies": e_resp.data or [],
    }


@router.get("/me")
async def whoami_inversor(invitacion_id: int = Depends(_require_inversor_cookie)):
    """Devuelve nombre del inversor actual (basado en la cookie)."""
    db = admin()
    resp = db.table("invitaciones_inversor").select(
        "nombre_inversor, total_accesos"
    ).eq("id", invitacion_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(401, detail="Inversor no encontrado")
    return {"ok": True, **resp.data[0]}


# ─────────────────── ADMIN: GENERAR CÓDIGOS ───────────────────

class GenerarCodigoRequest(BaseModel):
    nombre_inversor: str = Field(..., min_length=2, max_length=120)
    email: Optional[str] = None
    notas: Optional[str] = None
    dias_validez: Optional[int] = Field(default=None, ge=1, le=365)


@router.post("/codigos")
async def generar_codigo(
    req: GenerarCodigoRequest,
    user: UserContext = Depends(require_admin),
):
    """Admin genera un nuevo código de invitación para un inversor."""
    import secrets
    db = admin()

    # Genera código único: VO-INV-XXXXXX (6 caracteres alfanuméricos)
    for _ in range(5):
        codigo = "VO-INV-" + secrets.token_urlsafe(6).upper().replace("_", "").replace("-", "")[:6]
        existing = db.table("invitaciones_inversor").select("id").eq("codigo", codigo).limit(1).execute()
        if not existing.data:
            break
    else:
        raise HTTPException(500, detail="No se pudo generar código único")

    expira_en = None
    if req.dias_validez:
        from datetime import datetime, timedelta, timezone
        expira_en = (datetime.now(timezone.utc) + timedelta(days=req.dias_validez)).isoformat()

    insert = db.table("invitaciones_inversor").insert({
        "codigo": codigo,
        "nombre_inversor": req.nombre_inversor,
        "email": req.email,
        "notas": req.notas,
        "expira_en": expira_en,
        "activo": True,
    }).execute()

    return {
        "ok": True,
        "codigo": codigo,
        "url": f"{get_settings().app_base_url}/inversores?codigo={codigo}",
        "invitacion": insert.data[0] if insert.data else None,
    }


@router.get("/codigos")
async def listar_codigos(user: UserContext = Depends(require_admin)):
    """Admin lista todos los códigos emitidos."""
    db = admin()
    resp = db.table("invitaciones_inversor").select(
        "id, codigo, nombre_inversor, email, activo, "
        "expira_en, primer_uso_en, ultimo_uso_en, total_accesos, creado_en"
    ).order("creado_en", desc=True).execute()
    return {"ok": True, "items": resp.data or []}
