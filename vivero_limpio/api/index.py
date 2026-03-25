"""
ViveroOnline — Backend FastAPI para Vercel
==========================================
Todos los endpoints del marketplace AgTech B2B.
Vercel ejecuta este archivo como serverless function.
"""

import base64
import os
from io import BytesIO
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

load_dotenv()

# Importar módulos propios
import sys
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
from agent import analizar_planta_con_ia, chat_agente

# ─── APP ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="ViveroOnline API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    return {"status": "ok", "app": "ViveroOnline"}


# ─── AUTH ─────────────────────────────────────────────────────────────────────
@app.post("/api/login")
async def login(req: LoginRequest):
    viverista = obtener_viverista_por_email(req.email.strip().lower())
    if not viverista:
        raise HTTPException(status_code=404, detail="No encontramos ese correo.")
    registrar_evento_flywheel("login", viverista["id"])
    return {"ok": True, "viverista": viverista}


@app.post("/api/registro")
async def registro(req: RegistroRequest):
    existente = obtener_viverista_por_email(req.email.strip().lower())
    if existente:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese correo.")
    nuevo = registrar_viverista({
        "nombre":        req.nombre.strip(),
        "email":         req.email.strip().lower(),
        "telefono":      req.telefono or "",
        "municipio":     req.municipio,
        "nombre_vivero": req.nombre_vivero.strip(),
    })
    if not nuevo:
        raise HTTPException(status_code=500, detail="Error al crear la cuenta.")
    registrar_evento_flywheel("registro", nuevo["id"])
    return {"ok": True, "viverista": nuevo}


# ─── CATÁLOGO ─────────────────────────────────────────────────────────────────
@app.get("/api/catalogo/{viverista_id}")
async def get_catalogo(viverista_id: str):
    plantas = obtener_catalogo(viverista_id)
    return {"plantas": plantas}


@app.get("/api/marketplace")
async def get_marketplace(
    viverista_id: str,
    buscar: Optional[str] = None,
    municipio: Optional[str] = None,
    precio_max: Optional[float] = None,
):
    plantas = obtener_catalogo_marketplace(
        excluir_viverista_id=viverista_id,
        buscar=buscar,
        municipio=municipio,
        precio_max=precio_max,
    )
    return {"plantas": plantas}


# ─── VISIÓN IA ────────────────────────────────────────────────────────────────
@app.post("/api/analizar-planta")
async def analizar_planta(
    viverista_id: str = Form(...),
    municipio: str = Form("Cajicá"),
    precio_cop: float = Form(0),
    stock: int = Form(1),
    imagen: UploadFile = File(...),
):
    # Validar formato
    if imagen.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Formato no soportado. Usa JPEG, PNG o WebP.")

    # Leer y comprimir imagen
    content = await imagen.read()
    img = Image.open(BytesIO(content))
    if img.width > 800 or img.height > 800:
        img.thumbnail((800, 800), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    img_bytes = buf.getvalue()
    img_b64 = base64.b64encode(img_bytes).decode()

    # Analizar con Gemini Vision
    resultado = analizar_planta_con_ia(img_b64, "image/jpeg", municipio)
    if not resultado:
        raise HTTPException(500, "No se pudo analizar la imagen.")

    # Guardar en Supabase
    precio_final = precio_cop if precio_cop > 0 else resultado.get("precio_estimado_cop", 0)
    planta = agregar_planta_catalogo(
        viverista_id=viverista_id,
        datos=resultado,
        precio_cop=precio_final,
        stock=stock,
        imagen_bytes=img_bytes,
        imagen_nombre=imagen.filename,
    )

    registrar_evento_flywheel("vision_scan", viverista_id, {
        "planta": resultado.get("nombre_comun"),
        "confianza": resultado.get("confianza"),
    })

    return {
        "ok": True,
        "analisis": resultado,
        "planta_id": planta["id"] if planta else None,
    }


# ─── TRANSACCIONES ────────────────────────────────────────────────────────────
@app.post("/api/transaccion")
async def crear_transaccion_endpoint(req: TransaccionRequest):
    txn = crear_transaccion(
        viverista_id=req.viverista_id,
        comprador_id=req.comprador_id,
        planta_id=req.planta_id,
        cantidad=req.cantidad,
        precio_unitario=req.precio_unitario,
    )
    if not txn:
        raise HTTPException(500, "Error al crear el pedido.")
    registrar_evento_flywheel("transaccion", req.comprador_id, {
        "total_cop": req.cantidad * req.precio_unitario,
    })
    return {"ok": True, "transaccion": txn}


@app.get("/api/transacciones/{viverista_id}")
async def get_transacciones(viverista_id: str, tipo: str = "ventas"):
    txns = obtener_mis_transacciones(viverista_id, tipo)
    return {"transacciones": txns}


# ─── CHAT IA ──────────────────────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(req: ChatRequest):
    respuesta = chat_agente(
        mensaje=req.mensaje,
        municipio=req.municipio,
        historial=req.historial,
    )
    return {"respuesta": respuesta}


# ─── KPIs (dashboard inversor) ────────────────────────────────────────────────
@app.get("/api/kpis")
async def get_kpis():
    kpis = obtener_kpis()
    return {"kpis": kpis}
