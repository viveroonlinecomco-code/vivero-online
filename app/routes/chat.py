"""Chat con el orchestrator LangGraph (8 agentes)."""
import time
from fastapi import APIRouter, Depends

from app.auth.deps import UserContext, require_user
from app.agents import route_message, AgentContext
from app.schemas.transactions import ChatRequest, ChatResponse
from app.services.supabase import admin


router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, user: UserContext = Depends(require_user)):
    """Mensaje del usuario → LangGraph router → agente → respuesta."""
    # Construye contexto
    ctx = AgentContext(
        user_id=user.user_id,
        whatsapp=user.whatsapp,
        rol=user.rol,
        vivero_id=user.vivero_id,
        cliente_id=user.cliente_id,
    )

    # Carga municipio del usuario
    db = admin()
    if user.vivero_id:
        v = db.table("viveros").select("ciudad, latitud, longitud").eq(
            "vivero_id", user.vivero_id
        ).limit(1).execute()
        if v.data:
            ctx.municipio = v.data[0].get("ciudad") or ctx.municipio
            if v.data[0].get("latitud"):
                ctx.lat = float(v.data[0]["latitud"])
            if v.data[0].get("longitud"):
                ctx.lon = float(v.data[0]["longitud"])
    elif user.cliente_id:
        c = db.table("clientes").select("ciudad").eq(
            "cliente_id", user.cliente_id
        ).limit(1).execute()
        if c.data and c.data[0].get("ciudad"):
            ctx.municipio = c.data[0]["ciudad"]

    # Historial reciente
    ctx.historial = [
        {"role": "user" if i % 2 == 0 else "model", "content": m}
        for i, m in enumerate(req.historial[-6:])
    ]

    # Dispatch
    start = time.time()
    result = route_message(req.mensaje, ctx)
    latency_ms = int((time.time() - start) * 1000)

    # Log a log_ia (best effort, no bloquear respuesta si falla)
    try:
        db.table("log_ia").insert({
            "tipo_operacion": f"chat_{result.get('agente', 'unknown')}",
            "modelo_usado": "gemini-2.5-flash",
            "input_data": {"mensaje": req.mensaje[:500], "rol": user.rol},
            "output_data": {
                "respuesta": result.get("respuesta", "")[:2000],
                "metadata": result.get("metadata") or {},
            },
            "latencia_ms": latency_ms,
        }).execute()
    except Exception:
        pass

    return ChatResponse(
        ok=True,
        respuesta=result["respuesta"],
        agente=result["agente"],
        metadata=result.get("metadata", {}),
    )

