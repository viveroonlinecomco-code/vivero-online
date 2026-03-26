"""
ViveroOnline — Backend FastAPI completo
Pipeline: YOLO-11 + Gemini Vision + WhatsApp + Supabase
"""

import base64
import os
import sys
from io import BytesIO
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from PIL import Image
from pydantic import BaseModel

load_dotenv()
sys.path.append(os.path.dirname(__file__))

from db import (
    agregar_planta_catalogo,
    crear_transaccion,
    obtener_catalogo,
    obtener_catalogo_marketplace,
    obtener_kpis,
    obtener_mis_transacciones,
    obtener_viverista_por_email,
    registrar_evento_flywheel,
    registrar_viverista,
)
from agent import analizar_planta_con_ia, analizar_planta_pipeline, chat_agente

app = FastAPI(title="ViveroOnline API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ─── MODELOS ──────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str

class RegistroRequest(BaseModel):
    nombre: str
    email: str
    telefono: Optional[str] = ""
    municipio: str
    nombre_vivero: str

class TransaccionRequest(BaseModel):
    viverista_id: str
    comprador_id: str
    planta_id: str
    cantidad: int
    precio_unitario: float

class ChatRequest(BaseModel):
    mensaje: str
    municipio: str = "Cajicá"
    historial: List[str] = []

# ─── HEALTH ───────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.0", "pipeline": "YOLO-11 + Gemini"}

# ─── AUTH ─────────────────────────────────────────────────────────────────────
@app.post("/api/login")
async def login(req: LoginRequest):
    v = obtener_viverista_por_email(req.email.strip().lower())
    if not v:
        raise HTTPException(404, "No encontramos ese correo.")
    registrar_evento_flywheel("login", v["id"])
    return {"ok": True, "viverista": v}

@app.post("/api/registro")
async def registro(req: RegistroRequest):
    if obtener_viverista_por_email(req.email.strip().lower()):
        raise HTTPException(409, "Ya existe una cuenta con ese correo.")
    nuevo = registrar_viverista({
        "nombre": req.nombre.strip(), "email": req.email.strip().lower(),
        "telefono": req.telefono or "", "municipio": req.municipio,
        "nombre_vivero": req.nombre_vivero.strip(),
    })
    if not nuevo:
        raise HTTPException(500, "Error al crear la cuenta.")
    registrar_evento_flywheel("registro", nuevo["id"])
    return {"ok": True, "viverista": nuevo}

# ─── CATÁLOGO ─────────────────────────────────────────────────────────────────
@app.get("/api/catalogo/{viverista_id}")
async def get_catalogo(viverista_id: str):
    return {"plantas": obtener_catalogo(viverista_id)}

@app.get("/api/marketplace")
async def get_marketplace(
    viverista_id: str,
    buscar: Optional[str] = None,
    municipio: Optional[str] = None,
    precio_max: Optional[float] = None,
):
    plantas = obtener_catalogo_marketplace(
        excluir_viverista_id=viverista_id,
        buscar=buscar, municipio=municipio, precio_max=precio_max)
    return {"plantas": plantas}

# ─── VISIÓN IA — YOLO-11 + GEMINI ────────────────────────────────────────────
@app.post("/api/analizar-planta")
async def analizar_planta(
    viverista_id: str = Form(...),
    municipio: str = Form("Cajicá"),
    precio_cop: float = Form(0),
    stock: int = Form(1),
    imagen: UploadFile = File(...),
):
    """Pipeline completo: YOLO-11 detecta/recorta → Gemini identifica → Supabase guarda."""
    if imagen.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Formato no soportado. Usa JPEG, PNG o WebP.")

    imagen_bytes = await imagen.read()

    # Pipeline YOLO-11 + Gemini
    resultado = await analizar_planta_pipeline(imagen_bytes, municipio)

    if not resultado:
        raise HTTPException(500, "No se pudo analizar la imagen. Intenta con otra foto más clara.")

    # Comprimir imagen para guardar
    try:
        img = Image.open(__import__("io").BytesIO(imagen_bytes))
        if img.width > 800 or img.height > 800:
            img.thumbnail((800, 800), Image.LANCZOS)
        buf = __import__("io").BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_guardada = buf.getvalue()
    except Exception:
        img_guardada = imagen_bytes

    precio_final = precio_cop if precio_cop > 0 else resultado.get("precio_estimado_cop", 0)

    planta = agregar_planta_catalogo(
        viverista_id=viverista_id, datos=resultado,
        precio_cop=precio_final, stock=stock,
        imagen_bytes=img_guardada, imagen_nombre=imagen.filename,
    )

    registrar_evento_flywheel("vision_scan", viverista_id, {
        "planta": resultado.get("nombre_comun"),
        "confianza": resultado.get("confianza"),
        "yolo": resultado.get("yolo_deteccion", {}).get("yolo_activo", False),
    })

    return {
        "ok": True,
        "analisis": resultado,
        "planta_id": planta["id"] if planta else None,
        "yolo_activo": resultado.get("yolo_deteccion", {}).get("yolo_activo", False),
    }

# ─── TRANSACCIONES ────────────────────────────────────────────────────────────
@app.post("/api/transaccion")
async def crear_transaccion_endpoint(req: TransaccionRequest):
    txn = crear_transaccion(
        viverista_id=req.viverista_id, comprador_id=req.comprador_id,
        planta_id=req.planta_id, cantidad=req.cantidad,
        precio_unitario=req.precio_unitario,
    )
    if not txn:
        raise HTTPException(500, "Error al crear el pedido.")
    registrar_evento_flywheel("transaccion", req.comprador_id, {
        "total_cop": req.cantidad * req.precio_unitario})
    return {"ok": True, "transaccion": txn}

@app.get("/api/transacciones/{viverista_id}")
async def get_transacciones(viverista_id: str, tipo: str = "ventas"):
    return {"transacciones": obtener_mis_transacciones(viverista_id, tipo)}

# ─── CHAT IA ──────────────────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(req: ChatRequest):
    respuesta = chat_agente(req.mensaje, req.municipio, req.historial)
    return {"respuesta": respuesta}

# ─── KPIs ─────────────────────────────────────────────────────────────────────
@app.get("/api/kpis")
async def get_kpis():
    return {"kpis": obtener_kpis()}

# ─── WHATSAPP WEBHOOK ─────────────────────────────────────────────────────────
@app.post("/api/whatsapp")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(""),
    Body: str = Form(""),
    NumMedia: str = Form("0"),
    MediaUrl0: str = Form(""),
    MediaContentType0: str = Form(""),
):
    """
    Webhook de Twilio WhatsApp.
    El viverista envía foto → YOLO-11 + Gemini identifica → guarda en catálogo.
    Configurar en Twilio: https://tu-app.vercel.app/api/whatsapp
    """
    from whatsapp import procesar_whatsapp
    return await procesar_whatsapp(
        From=From, Body=Body,
        NumMedia=NumMedia,
        MediaUrl0=MediaUrl0,
        MediaContentType0=MediaContentType0,
    )
