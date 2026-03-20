"""
ViveroOnline — Capa de acceso a Supabase
========================================
Todas las operaciones de base de datos en un solo módulo.
Usa SUPABASE_SERVICE_KEY en el backend — nunca la anon key.
"""

import os
import uuid
from typing import Any, Dict, List, Optional

import streamlit as st
from supabase import Client, create_client


# ─── CONEXIÓN (singleton cacheado por Streamlit) ─────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["service_key"]
    except Exception:
        from dotenv import load_dotenv
        load_dotenv()
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


def _sb() -> Client:
    return get_supabase()


# ─── VIVERISTAS ───────────────────────────────────────────────────────────────
def registrar_viverista(datos: Dict) -> Optional[Dict]:
    """Crea un nuevo viverista. Devuelve el registro creado o None."""
    try:
        result = _sb().table("viveristas").insert({
            "nombre":        datos["nombre"],
            "email":         datos["email"],
            "telefono":      datos.get("telefono", ""),
            "municipio":     datos["municipio"],
            "nombre_vivero": datos.get("nombre_vivero", datos["nombre"]),
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        st.error(f"Error registrando viverista: {e}")
        return None


def obtener_viverista_por_email(email: str) -> Optional[Dict]:
    """Busca un viverista por email."""
    try:
        result = _sb().table("viveristas")\
            .select("*")\
            .eq("email", email)\
            .limit(1)\
            .execute()
        return result.data[0] if result.data else None
    except Exception:
        return None


# ─── CATÁLOGO DE PLANTAS ──────────────────────────────────────────────────────
def agregar_planta_catalogo(
    viverista_id: str,
    datos: Dict,
    precio_cop: float,
    stock: int,
    imagen_bytes: Optional[bytes] = None,
    imagen_nombre: Optional[str] = None,
) -> Optional[Dict]:
    """Agrega una planta al catálogo. Sube imagen a Storage si se provee."""
    imagen_url = None

    # 1. Subir imagen a Supabase Storage
    if imagen_bytes and imagen_nombre:
        try:
            ext = imagen_nombre.rsplit(".", 1)[-1].lower()
            storage_path = f"plantas/{viverista_id}/{uuid.uuid4()}.{ext}"
            _sb().storage.from_("plant-images").upload(
                storage_path,
                imagen_bytes,
                {"content-type": f"image/{ext}", "upsert": "true"},
            )
            imagen_url = _sb().storage\
                .from_("plant-images")\
                .get_public_url(storage_path)
        except Exception as e:
            # Si falla el storage, continuamos sin imagen
            pass

    # 2. Insertar en catalogo_plantas
    try:
        result = _sb().table("catalogo_plantas").insert({
            "viverista_id":      viverista_id,
            "nombre_comun":      datos.get("nombre_comun", "Planta"),
            "nombre_cientifico": datos.get("nombre_cientifico"),
            "descripcion":       datos.get("descripcion", ""),
            "precio_cop":        precio_cop or datos.get("precio_estimado_cop", 0),
            "stock_unidades":    stock,
            "imagen_url":        imagen_url,
            "vision_metadata":   datos,
            "verificado":        False,
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        st.error(f"Error guardando planta: {e}")
        return None


def obtener_catalogo(viverista_id: str) -> List[Dict]:
    """Devuelve todas las plantas del catálogo de un viverista."""
    try:
        result = _sb().table("catalogo_plantas")\
            .select("*")\
            .eq("viverista_id", viverista_id)\
            .order("created_at", desc=True)\
            .execute()
        return result.data or []
    except Exception:
        return []


def obtener_catalogo_marketplace(
    excluir_viverista_id: str,
    buscar: Optional[str] = None,
    municipio: Optional[str] = None,
    precio_max: Optional[float] = None,
) -> List[Dict]:
    """Devuelve catálogo de OTROS viveristas con datos del viverista dueño."""
    try:
        # Join manual: plantas + viveristas
        query = _sb().table("catalogo_plantas")\
            .select("*, viveristas(nombre, nombre_vivero, municipio, telefono)")\
            .neq("viverista_id", excluir_viverista_id)\
            .gt("stock_unidades", 0)

        if precio_max:
            query = query.lte("precio_cop", precio_max)

        result = query.order("created_at", desc=True).limit(50).execute()
        plantas = result.data or []

        # Aplanar el join y filtrar
        aplanadas = []
        for p in plantas:
            v_data = p.pop("viveristas", {}) or {}
            p["nombre_vivero"] = v_data.get("nombre_vivero", "")
            p["municipio"]     = v_data.get("municipio", "")
            p["telefono_vendedor"] = v_data.get("telefono", "")

            # Filtro de búsqueda por texto (cliente-side para simplicidad)
            if buscar:
                texto = f"{p.get('nombre_comun','')} {p.get('nombre_cientifico','')} {p.get('descripcion','')}".lower()
                if buscar.lower() not in texto:
                    continue
            # Filtro de municipio
            if municipio and p.get("municipio") != municipio:
                continue

            aplanadas.append(p)

        return aplanadas
    except Exception as e:
        return []


# ─── TRANSACCIONES B2B ────────────────────────────────────────────────────────
def crear_transaccion(
    viverista_id: str,
    comprador_id: str,
    planta_id: str,
    cantidad: int,
    precio_unitario: float,
) -> Optional[Dict]:
    """Crea una transacción B2B con estado 'pendiente'."""
    try:
        result = _sb().table("transacciones_b2b").insert({
            "viverista_id":        viverista_id,
            "comprador_id":        comprador_id,
            "planta_id":           planta_id,
            "cantidad":            cantidad,
            "precio_unitario_cop": precio_unitario,
            "estado":              "pendiente",
        }).execute()
        # Descontar stock
        if result.data:
            _sb().rpc("fn_descontar_stock", {
                "p_planta_id": planta_id,
                "p_cantidad":  cantidad,
            }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        st.error(f"Error creando transacción: {e}")
        return None


def obtener_mis_transacciones(viverista_id: str, tipo: str = "ventas") -> List[Dict]:
    """Devuelve ventas o compras del viverista con nombre de la planta y contraparte."""
    try:
        if tipo == "ventas":
            campo_filtro   = "viverista_id"
            campo_contraparte = "comprador_id"
        else:
            campo_filtro   = "comprador_id"
            campo_contraparte = "viverista_id"

        result = _sb().table("transacciones_b2b")\
            .select("*, catalogo_plantas(nombre_comun), viveristas!comprador_id(nombre)")\
            .eq(campo_filtro, viverista_id)\
            .order("created_at", desc=True)\
            .limit(50)\
            .execute()

        txns = []
        for t in (result.data or []):
            planta_data = t.pop("catalogo_plantas", {}) or {}
            contra_data = t.pop("viveristas", {}) or {}
            t["nombre_planta"]    = planta_data.get("nombre_comun", "Planta")
            t["contraparte_nombre"] = contra_data.get("nombre", "—")
            t["total_cop"] = t.get("cantidad", 0) * t.get("precio_unitario_cop", 0)
            txns.append(t)
        return txns
    except Exception:
        return []


# ─── FLYWHEEL / EVENTOS ───────────────────────────────────────────────────────
def registrar_evento_flywheel(
    tipo: str,
    viverista_id: Optional[str] = None,
    payload: Optional[Dict] = None,
) -> None:
    """Registra un evento del Data Flywheel. No bloquea si falla."""
    try:
        _sb().table("eventos_agente").insert({
            "tipo":         tipo,
            "viverista_id": viverista_id,
            "payload":      payload or {},
        }).execute()
    except Exception:
        pass  # Los eventos nunca rompen el flujo principal
