"""
ViveroOnline — MVP Streamlit
============================
Marketplace AgTech B2B · Sabana de Bogotá
Deploy: Streamlit Cloud  |  DB: Supabase  |  IA: Gemini 2.5 Flash
"""

import base64
import json
import os
from datetime import datetime
from io import BytesIO

import streamlit as st
from PIL import Image

# ── Configuración de página (DEBE ser la primera llamada Streamlit) ───────────
st.set_page_config(
    page_title="ViveroOnline — Marketplace",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

from db import (
    registrar_viverista,
    obtener_viverista_por_email,
    agregar_planta_catalogo,
    obtener_catalogo,
    obtener_catalogo_marketplace,
    crear_transaccion,
    obtener_mis_transacciones,
    registrar_evento_flywheel,
)
from agent import analizar_planta_con_ia, chat_agente

# ─── ESTADO DE SESIÓN ────────────────────────────────────────────────────────
def init_session():
    defaults = {
        "viverista": None,          # dict con datos del viverista logueado
        "pagina": "inicio",
        "chat_history": [],
        "catalogo_cache": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session()


# ─── CSS MÍNIMO ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card {
    background: #f0f7f0;
    border-left: 4px solid #1D9E75;
    padding: 1rem 1.2rem;
    border-radius: 8px;
    margin-bottom: 0.5rem;
}
.planta-card {
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    padding: 1rem;
    margin-bottom: 1rem;
    background: white;
}
.badge-activo { color: #0F6E56; font-weight: 600; }
.badge-pendiente { color: #BA7517; font-weight: 600; }
</style>
""", unsafe_allow_html=True)


# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.image("https://placehold.co/200x60/1D9E75/white?text=🌿+ViveroOnline", width=200)
        st.caption("Marketplace AgTech B2B · Sabana de Bogotá")
        st.divider()

        if st.session_state.viverista:
            v = st.session_state.viverista
            st.success(f"👋 Hola, **{v['nombre'].split()[0]}**")
            st.caption(f"📍 {v.get('municipio', 'Sabana de Bogotá')}")
            st.divider()

            opciones = {
                "🏠 Inicio":              "inicio",
                "🌿 Mi Catálogo":         "catalogo",
                "🛒 Marketplace":         "marketplace",
                "📋 Mis Transacciones":   "transacciones",
                "🤖 Agente IA":           "agente",
            }
            for label, pagina in opciones.items():
                activo = st.session_state.pagina == pagina
                if st.button(label, use_container_width=True,
                             type="primary" if activo else "secondary"):
                    st.session_state.pagina = pagina
                    st.rerun()

            st.divider()
            if st.button("🚪 Cerrar sesión", use_container_width=True):
                st.session_state.viverista = None
                st.session_state.pagina = "inicio"
                st.rerun()
        else:
            st.info("Inicia sesión para acceder al marketplace")


# ─── PÁGINA: INICIO / LOGIN ───────────────────────────────────────────────────
def pagina_inicio():
    col1, col2 = st.columns([1, 1], gap="large")

    with col1:
        st.markdown("## 🌿 ViveroOnline")
        st.markdown("### El marketplace B2B de la Sabana de Bogotá")
        st.markdown("""
Conectamos viveristas con compradores profesionales:
- 🏗️ **Constructoras y paisajistas**
- 🏢 **Empresas con jardines corporativos**
- 🏘️ **Conjuntos residenciales**
- 🌳 **Proyectos de reforestación**

**IA integrada** que identifica tus plantas con solo una foto.
        """)

        st.markdown("---")
        st.markdown("#### ¿Ya tienes cuenta?")
        email_login = st.text_input("Correo electrónico", key="email_login",
                                    placeholder="tu@vivero.com")
        if st.button("🚀 Ingresar", use_container_width=True, type="primary"):
            if email_login:
                v = obtener_viverista_por_email(email_login.strip().lower())
                if v:
                    st.session_state.viverista = v
                    st.session_state.pagina = "catalogo"
                    registrar_evento_flywheel("login", v["id"])
                    st.rerun()
                else:
                    st.error("No encontramos ese correo. ¿Ya te registraste?")
            else:
                st.warning("Ingresa tu correo")

    with col2:
        st.markdown("#### Registra tu vivero")
        with st.form("registro_form"):
            nombre    = st.text_input("Nombre completo *", placeholder="Carlos Pérez")
            email     = st.text_input("Correo *", placeholder="carlos@mivivero.com")
            telefono  = st.text_input("Teléfono WhatsApp", placeholder="3001234567")
            municipio = st.selectbox("Municipio *", [
                "Cajicá", "Zipaquirá", "Chía", "Sopó", "La Calera",
                "Tabio", "Tenjo", "Facatativá", "Madrid", "Mosquera",
                "Funza", "Cota", "Tocancipá", "Gachancipá", "Otro"
            ])
            nombre_vivero = st.text_input("Nombre de tu vivero *",
                                          placeholder="Vivero Las Margaritas")
            submitted = st.form_submit_button("🌱 Crear mi cuenta", use_container_width=True,
                                              type="primary")

        if submitted:
            if not all([nombre, email, municipio, nombre_vivero]):
                st.error("Completa los campos obligatorios (*)")
            else:
                existente = obtener_viverista_por_email(email.strip().lower())
                if existente:
                    st.warning("Ya existe una cuenta con ese correo. Inicia sesión.")
                else:
                    nuevo = registrar_viverista({
                        "nombre":        nombre.strip(),
                        "email":         email.strip().lower(),
                        "telefono":      telefono.strip(),
                        "municipio":     municipio,
                        "nombre_vivero": nombre_vivero.strip(),
                    })
                    if nuevo:
                        st.session_state.viverista = nuevo
                        st.session_state.pagina = "catalogo"
                        registrar_evento_flywheel("registro", nuevo["id"])
                        st.success(f"✅ ¡Bienvenido, {nombre.split()[0]}!")
                        st.rerun()
                    else:
                        st.error("Error al crear la cuenta. Intenta de nuevo.")


# ─── PÁGINA: MI CATÁLOGO ─────────────────────────────────────────────────────
def pagina_catalogo():
    v = st.session_state.viverista
    st.markdown(f"## 🌿 Mi Catálogo — {v.get('nombre_vivero', v['nombre'])}")

    tab1, tab2 = st.tabs(["📋 Ver mi catálogo", "➕ Agregar planta"])

    # ── TAB 1: Ver catálogo ──
    with tab1:
        plantas = obtener_catalogo(v["id"])
        if not plantas:
            st.info("Tu catálogo está vacío. ¡Agrega tu primera planta con IA!")
        else:
            st.caption(f"{len(plantas)} plantas en tu catálogo")
            for i in range(0, len(plantas), 3):
                cols = st.columns(3)
                for j, col in enumerate(cols):
                    if i + j < len(plantas):
                        p = plantas[i + j]
                        with col:
                            with st.container(border=True):
                                if p.get("imagen_url"):
                                    st.image(p["imagen_url"], use_container_width=True)
                                st.markdown(f"**{p['nombre_comun']}**")
                                if p.get("nombre_cientifico"):
                                    st.caption(f"*{p['nombre_cientifico']}*")
                                st.metric("Precio",
                                    f"${p.get('precio_cop', 0):,.0f} COP")
                                st.caption(f"Stock: {p.get('stock_unidades', 0)} uds")
                                if p.get("descripcion"):
                                    with st.expander("Ver descripción"):
                                        st.write(p["descripcion"])

    # ── TAB 2: Agregar planta ──
    with tab2:
        st.markdown("### IA identifica tu planta automáticamente")
        st.caption("Sube una foto y Gemini 2.5 Flash analiza la planta en segundos")

        col_form, col_preview = st.columns([1, 1])

        with col_form:
            imagen_file = st.file_uploader(
                "📸 Foto de la planta",
                type=["jpg", "jpeg", "png", "webp"],
                help="Foto clara, con buena luz, fondo neutro preferible"
            )

            precio_manual = st.number_input(
                "Precio de venta (COP)", min_value=0, value=0, step=1000,
                help="Puedes dejarlo en 0 y la IA sugerirá un precio"
            )
            stock = st.number_input("Unidades disponibles", min_value=0, value=1, step=1)

            analizar = st.button("🤖 Analizar con IA y agregar al catálogo",
                                 use_container_width=True, type="primary",
                                 disabled=imagen_file is None)

        with col_preview:
            if imagen_file:
                img = Image.open(imagen_file)
                st.image(img, caption="Vista previa", use_container_width=True)

        if analizar and imagen_file:
            with st.spinner("🌿 Gemini está analizando tu planta..."):
                # Comprimir imagen
                img = Image.open(imagen_file)
                if img.width > 800 or img.height > 800:
                    img.thumbnail((800, 800), Image.LANCZOS)
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=85)
                img_bytes = buf.getvalue()
                img_b64 = base64.b64encode(img_bytes).decode()

                # Llamar al agente de visión
                resultado = analizar_planta_con_ia(
                    img_b64, "image/jpeg", v["municipio"]
                )

            if resultado:
                st.success(f"✅ Identificada: **{resultado.get('nombre_comun', 'Planta')}**")

                col_r1, col_r2, col_r3 = st.columns(3)
                col_r1.metric("Nombre científico",
                    resultado.get("nombre_cientifico", "—"))
                col_r2.metric("Precio sugerido IA",
                    f"${resultado.get('precio_estimado_cop', 0):,} COP")
                col_r3.metric("Confianza",
                    f"{resultado.get('confianza', 0)*100:.0f}%")

                with st.expander("📝 Descripción y cuidados"):
                    st.write(resultado.get("descripcion", ""))
                    st.write(resultado.get("cuidados", ""))
                if resultado.get("advertencias"):
                    st.warning(f"⚠️ {resultado['advertencias']}")

                precio_final = precio_manual if precio_manual > 0 \
                    else resultado.get("precio_estimado_cop", 0)

                if st.button("💾 Confirmar y guardar en catálogo",
                             use_container_width=True, type="primary"):
                    # Subir imagen a Supabase Storage y guardar planta
                    planta_guardada = agregar_planta_catalogo(
                        viverista_id=v["id"],
                        datos=resultado,
                        precio_cop=precio_final,
                        stock=stock,
                        imagen_bytes=img_bytes,
                        imagen_nombre=imagen_file.name,
                    )
                    if planta_guardada:
                        registrar_evento_flywheel("vision_scan", v["id"], {
                            "planta": resultado.get("nombre_comun"),
                            "confianza": resultado.get("confianza"),
                        })
                        st.success(
                            f"🌿 **{resultado['nombre_comun']}** agregada a tu catálogo!"
                        )
                        st.session_state.catalogo_cache = None  # invalidar cache
                        st.rerun()
                    else:
                        st.error("Error guardando la planta. Intenta de nuevo.")
            else:
                st.error("No se pudo analizar la imagen. Intenta con otra foto.")


# ─── PÁGINA: MARKETPLACE ─────────────────────────────────────────────────────
def pagina_marketplace():
    v = st.session_state.viverista
    st.markdown("## 🛒 Marketplace — Catálogo General")
    st.caption("Plantas disponibles de todos los viveristas de la Sabana de Bogotá")

    # Filtros
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        buscar = st.text_input("🔍 Buscar planta", placeholder="sansevieria, helecho...")
    with col_f2:
        municipio_filtro = st.selectbox("Municipio", ["Todos", "Cajicá", "Zipaquirá",
            "Chía", "Sopó", "La Calera", "Tabio", "Tenjo"])
    with col_f3:
        precio_max = st.number_input("Precio máximo (COP)", value=500000, step=10000)

    plantas = obtener_catalogo_marketplace(
        excluir_viverista_id=v["id"],
        buscar=buscar if buscar else None,
        municipio=municipio_filtro if municipio_filtro != "Todos" else None,
        precio_max=precio_max,
    )

    if not plantas:
        st.info("No hay plantas disponibles con esos filtros. Intenta con otros criterios.")
        return

    st.caption(f"{len(plantas)} plantas disponibles")

    for i in range(0, len(plantas), 3):
        cols = st.columns(3)
        for j, col in enumerate(cols):
            if i + j < len(plantas):
                p = plantas[i + j]
                with col:
                    with st.container(border=True):
                        if p.get("imagen_url"):
                            st.image(p["imagen_url"], use_container_width=True)
                        st.markdown(f"**{p['nombre_comun']}**")
                        if p.get("nombre_cientifico"):
                            st.caption(f"*{p['nombre_cientifico']}*")
                        st.caption(
                            f"🌱 {p.get('nombre_vivero', 'Vivero')} · "
                            f"📍 {p.get('municipio', '')}"
                        )
                        col_p, col_s = st.columns(2)
                        col_p.metric("Precio", f"${p.get('precio_cop', 0):,.0f}")
                        col_s.metric("Stock", p.get("stock_unidades", 0))

                        cantidad = st.number_input(
                            "Cantidad", min_value=1,
                            max_value=max(1, p.get("stock_unidades", 1)),
                            value=1, key=f"qty_{p['id']}"
                        )
                        if st.button(f"🛒 Pedir ahora",
                                     key=f"pedir_{p['id']}",
                                     use_container_width=True,
                                     type="primary"):
                            txn = crear_transaccion(
                                viverista_id=p["viverista_id"],
                                comprador_id=v["id"],
                                planta_id=p["id"],
                                cantidad=cantidad,
                                precio_unitario=p["precio_cop"],
                            )
                            if txn:
                                registrar_evento_flywheel("transaccion", v["id"], {
                                    "planta": p["nombre_comun"],
                                    "total_cop": cantidad * p["precio_cop"],
                                })
                                st.success(
                                    f"✅ Pedido enviado: {cantidad} × {p['nombre_comun']} "
                                    f"= ${cantidad * p['precio_cop']:,} COP\n\n"
                                    f"El viverista recibirá tu pedido."
                                )
                            else:
                                st.error("Error al crear el pedido. Intenta de nuevo.")


# ─── PÁGINA: TRANSACCIONES ───────────────────────────────────────────────────
def pagina_transacciones():
    v = st.session_state.viverista
    st.markdown("## 📋 Mis Transacciones")

    tab_ventas, tab_compras = st.tabs(["📤 Mis ventas", "📥 Mis compras"])

    with tab_ventas:
        ventas = obtener_mis_transacciones(v["id"], tipo="ventas")
        if not ventas:
            st.info("Aún no tienes ventas. Comparte tu catálogo con compradores.")
        else:
            total = sum(t.get("total_cop", 0) for t in ventas
                       if t.get("estado") == "completada")
            st.metric("Total facturado", f"${total:,.0f} COP")
            for txn in ventas:
                _render_transaccion(txn, es_vendedor=True)

    with tab_compras:
        compras = obtener_mis_transacciones(v["id"], tipo="compras")
        if not compras:
            st.info("Aún no has realizado compras. Explora el marketplace.")
        else:
            for txn in compras:
                _render_transaccion(txn, es_vendedor=False)


def _render_transaccion(txn: dict, es_vendedor: bool):
    estado_color = {
        "pendiente":  "🟡",
        "confirmada": "🔵",
        "enviada":    "🟠",
        "completada": "🟢",
        "cancelada":  "🔴",
    }
    icon = estado_color.get(txn.get("estado", "pendiente"), "⚪")
    with st.container(border=True):
        col1, col2, col3, col4 = st.columns([3, 2, 2, 1])
        col1.markdown(f"**{txn.get('nombre_planta', 'Planta')}**")
        col2.write(f"{txn.get('cantidad', 0)} uds × ${txn.get('precio_unitario_cop', 0):,}")
        col3.metric("Total", f"${txn.get('total_cop', 0):,.0f} COP")
        col4.write(f"{icon} {txn.get('estado', '').capitalize()}")
        st.caption(
            f"{'Comprador' if es_vendedor else 'Vendedor'}: "
            f"{txn.get('contraparte_nombre', '—')} · "
            f"{txn.get('created_at', '')[:10]}"
        )


# ─── PÁGINA: AGENTE IA ───────────────────────────────────────────────────────
def pagina_agente():
    v = st.session_state.viverista
    st.markdown("## 🤖 Agente IA — ViveroOnline")
    st.caption("Tu asesor personal de plantas, mercado y negocios para la Sabana de Bogotá")

    # Historial de chat
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input del usuario
    if prompt := st.chat_input("Pregúntame sobre plantas, precios, paisajismo..."):
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("🌿 Consultando con los agentes..."):
                respuesta = chat_agente(
                    mensaje=prompt,
                    viverista_id=v["id"],
                    municipio=v.get("municipio", "Cajicá"),
                )
            st.markdown(respuesta)
            st.session_state.chat_history.append(
                {"role": "assistant", "content": respuesta}
            )

        registrar_evento_flywheel("chat", v["id"], {"mensaje": prompt[:100]})

    # Sugerencias rápidas
    if not st.session_state.chat_history:
        st.markdown("#### 💡 Preguntas frecuentes")
        sugerencias = [
            "¿Qué plantas se venden mejor en la Sabana de Bogotá?",
            "¿Cuánto debería cobrar por una Sansevieria grande?",
            "¿Cómo preparo mi catálogo para vender a constructoras?",
            "Tengo 50 helechos, ¿cómo los vendo en B2B?",
        ]
        for sug in sugerencias:
            if st.button(sug, use_container_width=True):
                st.session_state.chat_history.append({"role": "user", "content": sug})
                st.rerun()


# ─── ROUTER PRINCIPAL ─────────────────────────────────────────────────────────
def main():
    render_sidebar()

    if not st.session_state.viverista:
        pagina_inicio()
        return

    pagina = st.session_state.pagina
    if pagina == "inicio" or pagina == "catalogo":
        pagina_catalogo()
    elif pagina == "marketplace":
        pagina_marketplace()
    elif pagina == "transacciones":
        pagina_transacciones()
    elif pagina == "agente":
        pagina_agente()
    else:
        pagina_catalogo()


if __name__ == "__main__":
    main()
