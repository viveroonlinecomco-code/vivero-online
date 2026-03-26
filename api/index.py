"""
ViveroOnline — Backend FastAPI completo para Vercel
Pipeline: YOLO-11 + Gemini Vision + WhatsApp + Supabase
El frontend HTML se sirve directamente desde FastAPI.
"""

import base64
import os
import sys
from io import BytesIO
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
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

# ─── HTML FRONTEND (servido directamente por FastAPI) ─────────────────────────
HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ViveroOnline — Marketplace AgTech B2B</title>
<style>
  :root {
    --green: #1D9E75; --green-light: #E1F5EE; --green-dark: #0F6E56;
    --gray: #f8f9fa; --border: #e0e0e0; --text: #1a1a1a; --muted: #6c757d;
    --white: #ffffff; --danger: #dc3545; --warning: #ffc107; --info: #0dcaf0;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
  body { background: var(--gray); color: var(--text); min-height: 100vh; }

  /* NAV */
  nav { background: var(--white); border-bottom: 1px solid var(--border); padding: 0 1.5rem; display: flex; align-items: center; justify-content: space-between; height: 60px; position: sticky; top: 0; z-index: 100; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
  .nav-brand { font-size: 1.2rem; font-weight: 700; color: var(--green); display: flex; align-items: center; gap: 8px; }
  .nav-user { display: flex; align-items: center; gap: 12px; font-size: .9rem; }
  .nav-links { display: flex; gap: 4px; }
  .nav-btn { background: none; border: none; padding: 6px 12px; border-radius: 8px; cursor: pointer; font-size: .88rem; color: var(--muted); transition: all .15s; }
  .nav-btn:hover, .nav-btn.active { background: var(--green-light); color: var(--green-dark); font-weight: 500; }
  .btn-logout { color: var(--danger); }

  /* PAGES */
  .page { display: none; padding: 1.5rem; max-width: 1100px; margin: 0 auto; }
  .page.active { display: block; }

  /* CARDS */
  .card { background: var(--white); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem; }
  .card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }
  .plant-card { background: var(--white); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; transition: box-shadow .2s; }
  .plant-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.1); }
  .plant-card img { width: 100%; height: 180px; object-fit: cover; background: var(--green-light); }
  .plant-card .img-placeholder { width: 100%; height: 180px; background: var(--green-light); display: flex; align-items: center; justify-content: center; font-size: 3rem; }
  .plant-card-body { padding: 1rem; }
  .plant-name { font-weight: 600; font-size: 1rem; margin-bottom: 2px; }
  .plant-sci { font-size: .8rem; color: var(--muted); font-style: italic; margin-bottom: 8px; }
  .plant-price { font-size: 1.1rem; font-weight: 700; color: var(--green); }
  .plant-stock { font-size: .8rem; color: var(--muted); }
  .plant-meta { font-size: .78rem; color: var(--muted); margin: 6px 0; }

  /* FORMS */
  .form-group { margin-bottom: 1rem; }
  label { display: block; font-size: .88rem; font-weight: 500; margin-bottom: 4px; color: var(--text); }
  input, select, textarea { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: .95rem; outline: none; transition: border .15s; background: var(--white); }
  input:focus, select:focus, textarea:focus { border-color: var(--green); }
  .btn { padding: 10px 20px; border: none; border-radius: 8px; font-size: .95rem; font-weight: 500; cursor: pointer; transition: all .15s; display: inline-flex; align-items: center; gap: 6px; }
  .btn-primary { background: var(--green); color: white; }
  .btn-primary:hover { background: var(--green-dark); }
  .btn-outline { background: white; color: var(--green); border: 1px solid var(--green); }
  .btn-outline:hover { background: var(--green-light); }
  .btn-danger { background: var(--danger); color: white; }
  .btn-sm { padding: 6px 12px; font-size: .85rem; }
  .btn-full { width: 100%; justify-content: center; }
  .btn:disabled { opacity: .6; cursor: not-allowed; }

  /* ALERTS */
  .alert { padding: 12px 16px; border-radius: 8px; font-size: .9rem; margin-bottom: 1rem; }
  .alert-success { background: var(--green-light); color: var(--green-dark); border: 1px solid #9FE1CB; }
  .alert-danger { background: #fef2f2; color: #991b1b; border: 1px solid #fca5a5; }
  .alert-info { background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; }

  /* TABS */
  .tabs { display: flex; gap: 4px; border-bottom: 2px solid var(--border); margin-bottom: 1.5rem; }
  .tab { padding: 8px 16px; background: none; border: none; cursor: pointer; font-size: .9rem; color: var(--muted); border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all .15s; }
  .tab.active { color: var(--green); border-bottom-color: var(--green); font-weight: 500; }

  /* KPIs */
  .kpi-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
  .kpi-card { background: var(--white); border: 1px solid var(--border); border-radius: 12px; padding: 1rem 1.2rem; border-left: 4px solid var(--green); }
  .kpi-value { font-size: 1.8rem; font-weight: 700; color: var(--green); }
  .kpi-label { font-size: .8rem; color: var(--muted); margin-top: 4px; }

  /* CHAT */
  .chat-box { height: 400px; overflow-y: auto; border: 1px solid var(--border); border-radius: 12px; padding: 1rem; background: var(--gray); display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 80%; padding: 10px 14px; border-radius: 12px; font-size: .92rem; line-height: 1.5; }
  .msg-user { background: var(--green); color: white; align-self: flex-end; border-radius: 12px 12px 4px 12px; }
  .msg-bot { background: var(--white); border: 1px solid var(--border); align-self: flex-start; border-radius: 12px 12px 12px 4px; }
  .chat-input-row { display: flex; gap: 8px; margin-top: .8rem; }
  .chat-input-row input { flex: 1; }

  /* UPLOAD */
  .upload-zone { border: 2px dashed var(--border); border-radius: 12px; padding: 2rem; text-align: center; cursor: pointer; transition: all .2s; background: var(--gray); }
  .upload-zone:hover { border-color: var(--green); background: var(--green-light); }
  .upload-zone.dragover { border-color: var(--green); background: var(--green-light); }

  /* BADGE */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: .75rem; font-weight: 500; }
  .badge-pendiente { background: #fff3cd; color: #856404; }
  .badge-completada { background: var(--green-light); color: var(--green-dark); }
  .badge-cancelada { background: #fee2e2; color: #991b1b; }

  /* UTILS */
  .text-muted { color: var(--muted); }
  .text-center { text-align: center; }
  .mt-1 { margin-top: .5rem; } .mt-2 { margin-top: 1rem; } .mt-3 { margin-top: 1.5rem; }
  .flex { display: flex; } .gap-1 { gap: .5rem; } .gap-2 { gap: 1rem; }
  .items-center { align-items: center; } .justify-between { justify-content: space-between; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
  h2 { font-size: 1.4rem; margin-bottom: 1rem; }
  h3 { font-size: 1.1rem; margin-bottom: .8rem; }
  .spinner { display: inline-block; width: 18px; height: 18px; border: 2px solid rgba(255,255,255,.3); border-top-color: white; border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .hidden { display: none !important; }

  /* LOGIN PAGE */
  .login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 1.5rem; background: linear-gradient(135deg, #E1F5EE 0%, #f8f9fa 100%); }
  .login-box { background: white; border-radius: 16px; padding: 2.5rem; width: 100%; max-width: 900px; box-shadow: 0 8px 40px rgba(0,0,0,.08); display: grid; grid-template-columns: 1fr 1fr; gap: 3rem; }
  @media (max-width: 700px) { .login-box { grid-template-columns: 1fr; } .grid-2 { grid-template-columns: 1fr; } }
  .login-hero h1 { font-size: 2rem; color: var(--green); margin-bottom: .5rem; }
  .login-hero p { color: var(--muted); line-height: 1.6; margin-bottom: 1rem; }
  .feature-list { list-style: none; }
  .feature-list li { padding: 6px 0; color: var(--text); font-size: .95rem; }
  .feature-list li::before { content: "✓ "; color: var(--green); font-weight: 700; }
  .divider { border: none; border-top: 1px solid var(--border); margin: 1.2rem 0; }

  /* TXNS */
  .txn-row { display: grid; grid-template-columns: 2fr 1.5fr 1.5fr 1fr auto; gap: 1rem; align-items: center; padding: 1rem; border-bottom: 1px solid var(--border); font-size: .9rem; }
  .txn-row:last-child { border-bottom: none; }

  /* CANTIDAD */
  .qty-input { width: 70px; text-align: center; }

  #preview-img { max-width: 100%; border-radius: 8px; margin-top: 1rem; }
</style>
</head>
<body>

<!-- ── LOGIN PAGE ──────────────────────────────────────────────────────── -->
<div id="login-page" class="login-wrap">
  <div class="login-box">
    <div class="login-hero">
      <div style="font-size:2.5rem;margin-bottom:.5rem">🌿</div>
      <h1>ViveroOnline</h1>
      <p>El marketplace B2B de plantas para la Sabana de Bogotá. Conectamos viveristas con compradores profesionales.</p>
      <ul class="feature-list">
        <li>IA identifica tus plantas con una foto</li>
        <li>Catálogo digital en minutos</li>
        <li>Compras y ventas B2B seguras</li>
        <li>Paisajistas, constructoras y más</li>
      </ul>
    </div>
    <div>
      <!-- LOGIN -->
      <h3>¿Ya tienes cuenta?</h3>
      <div id="login-alert"></div>
      <div class="form-group">
        <label>Correo electrónico</label>
        <input type="email" id="login-email" placeholder="tu@vivero.com">
      </div>
      <button class="btn btn-primary btn-full" onclick="login()">
        🚀 Ingresar
      </button>

      <hr class="divider">

      <!-- REGISTRO -->
      <h3>Crear cuenta nueva</h3>
      <div id="reg-alert"></div>
      <div class="grid-2">
        <div class="form-group">
          <label>Nombre completo *</label>
          <input type="text" id="reg-nombre" placeholder="Carlos Pérez">
        </div>
        <div class="form-group">
          <label>Correo *</label>
          <input type="email" id="reg-email" placeholder="carlos@vivero.com">
        </div>
        <div class="form-group">
          <label>WhatsApp</label>
          <input type="tel" id="reg-telefono" placeholder="3001234567">
        </div>
        <div class="form-group">
          <label>Municipio *</label>
          <select id="reg-municipio">
            <option>Cajicá</option><option>Zipaquirá</option><option>Chía</option>
            <option>Sopó</option><option>La Calera</option><option>Tabio</option>
            <option>Tenjo</option><option>Facatativá</option><option>Madrid</option>
            <option>Mosquera</option><option>Funza</option><option>Cota</option>
            <option>Tocancipá</option><option>Otro</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Nombre de tu vivero *</label>
        <input type="text" id="reg-vivero" placeholder="Vivero Las Margaritas">
      </div>
      <button class="btn btn-outline btn-full" onclick="registro()">
        🌱 Crear mi cuenta
      </button>
    </div>
  </div>
</div>

<!-- ── APP (after login) ───────────────────────────────────────────────── -->
<div id="app" class="hidden">
  <!-- NAVBAR -->
  <nav>
    <div class="nav-brand">🌿 ViveroOnline</div>
    <div class="nav-links">
      <button class="nav-btn active" onclick="showPage('catalogo')">🌿 Mi Catálogo</button>
      <button class="nav-btn" onclick="showPage('marketplace')">🛒 Marketplace</button>
      <button class="nav-btn" onclick="showPage('transacciones')">📋 Transacciones</button>
      <button class="nav-btn" onclick="showPage('agente')">🤖 Agente IA</button>
    </div>
    <div class="nav-user">
      <span id="nav-nombre" class="text-muted" style="font-size:.85rem"></span>
      <button class="nav-btn btn-logout" onclick="logout()">Salir</button>
    </div>
  </nav>

  <!-- CATÁLOGO -->
  <div id="page-catalogo" class="page active">
    <div class="flex justify-between items-center" style="margin-bottom:1rem">
      <h2>🌿 Mi Catálogo</h2>
    </div>
    <div class="tabs">
      <button class="tab active" onclick="showTab('tab-ver', this)">📋 Ver mis plantas</button>
      <button class="tab" onclick="showTab('tab-agregar', this)">➕ Agregar con IA</button>
    </div>

    <!-- VER CATÁLOGO -->
    <div id="tab-ver">
      <div id="mis-plantas" class="card-grid">
        <div class="text-center text-muted" style="padding:2rem;grid-column:1/-1">Cargando...</div>
      </div>
    </div>

    <!-- AGREGAR PLANTA -->
    <div id="tab-agregar" class="hidden">
      <div class="grid-2">
        <div class="card">
          <h3>📸 Sube una foto de tu planta</h3>
          <p class="text-muted" style="font-size:.85rem;margin-bottom:1rem">La IA de Gemini la identifica automáticamente</p>
          <div class="upload-zone" id="upload-zone" onclick="document.getElementById('file-input').click()">
            <div style="font-size:2rem">📷</div>
            <p style="margin-top:.5rem;color:var(--muted)">Clic aquí o arrastra tu foto</p>
            <p style="font-size:.8rem;color:var(--muted)">JPEG, PNG o WebP</p>
          </div>
          <input type="file" id="file-input" accept="image/jpeg,image/png,image/webp" class="hidden" onchange="onFileSelected(this)">
          <img id="preview-img" class="hidden">

          <div class="form-group mt-2">
            <label>Precio de venta (COP) — opcional</label>
            <input type="number" id="precio-manual" placeholder="0 = la IA sugiere el precio" min="0" step="1000">
          </div>
          <div class="form-group">
            <label>Unidades disponibles</label>
            <input type="number" id="stock-manual" value="1" min="1">
          </div>
          <button class="btn btn-primary btn-full" id="btn-analizar" onclick="analizarPlanta()" disabled>
            🤖 Analizar con IA
          </button>
        </div>

        <div class="card" id="resultado-analisis" class="hidden" style="display:none">
          <h3>✅ Resultado del análisis</h3>
          <div id="analisis-content"></div>
          <button class="btn btn-primary btn-full mt-2" id="btn-confirmar" onclick="confirmarPlanta()">
            💾 Confirmar y guardar en catálogo
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- MARKETPLACE -->
  <div id="page-marketplace" class="page">
    <h2>🛒 Marketplace — Plantas disponibles</h2>
    <div class="card" style="padding:1rem;margin-bottom:1rem">
      <div class="grid-2" style="gap:.8rem">
        <input type="text" id="buscar-input" placeholder="🔍 Buscar planta..." oninput="filtrarMarketplace()">
        <select id="municipio-filter" onchange="filtrarMarketplace()">
          <option value="">Todos los municipios</option>
          <option>Cajicá</option><option>Zipaquirá</option><option>Chía</option>
          <option>Sopó</option><option>La Calera</option><option>Tabio</option>
          <option>Tenjo</option><option>Facatativá</option>
        </select>
      </div>
    </div>
    <div id="marketplace-plantas" class="card-grid">
      <div class="text-center text-muted" style="padding:2rem;grid-column:1/-1">Cargando marketplace...</div>
    </div>
  </div>

  <!-- TRANSACCIONES -->
  <div id="page-transacciones" class="page">
    <h2>📋 Mis Transacciones</h2>
    <div class="tabs">
      <button class="tab active" onclick="showTab('tab-ventas', this); cargarVentas()">📤 Mis ventas</button>
      <button class="tab" onclick="showTab('tab-compras', this); cargarCompras()">📥 Mis compras</button>
    </div>
    <div id="tab-ventas">
      <div class="card" id="ventas-content">
        <div class="text-center text-muted" style="padding:2rem">Cargando ventas...</div>
      </div>
    </div>
    <div id="tab-compras" class="hidden">
      <div class="card" id="compras-content">
        <div class="text-center text-muted" style="padding:2rem">Cargando compras...</div>
      </div>
    </div>
  </div>

  <!-- AGENTE IA -->
  <div id="page-agente" class="page">
    <h2>🤖 Agente IA — Asesor ViveroOnline</h2>
    <p class="text-muted" style="margin-bottom:1rem">Tu asesor de plantas, precios y mercado para la Sabana de Bogotá</p>

    <div class="card">
      <div id="chat-box" class="chat-box">
        <div class="msg msg-bot">👋 ¡Hola! Soy tu asesor de ViveroOnline. Puedo ayudarte con precios, identificar plantas, consejos de mercado y más. ¿En qué te ayudo hoy?</div>
      </div>
      <div class="chat-input-row">
        <input type="text" id="chat-input" placeholder="Pregúntame sobre plantas, precios, paisajismo..." onkeydown="if(event.key==='Enter') enviarChat()">
        <button class="btn btn-primary" onclick="enviarChat()">Enviar</button>
      </div>
    </div>

    <div class="card mt-2">
      <h3>💡 Preguntas frecuentes</h3>
      <div style="display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.5rem">
        <button class="btn btn-outline btn-sm" onclick="preguntaRapida('¿Qué plantas se venden mejor en la Sabana de Bogotá?')">¿Qué plantas vender?</button>
        <button class="btn btn-outline btn-sm" onclick="preguntaRapida('¿Cuánto debería cobrar por una Sansevieria grande?')">Precio Sansevieria</button>
        <button class="btn btn-outline btn-sm" onclick="preguntaRapida('¿Cómo vender plantas a constructoras y paisajistas?')">Ventas B2B</button>
        <button class="btn btn-outline btn-sm" onclick="preguntaRapida('¿Qué plantas son buenas para clima frío de 2600 msnm?')">Clima frío</button>
      </div>
    </div>
  </div>
</div>

<script>
// ── STATE ──────────────────────────────────────────────────────────────────
let state = {
  viverista: null,
  archivoSeleccionado: null,
  ultimoAnalisis: null,
  chatHistory: [],
  todasLasPlantas: [],
};

const API = '/api';

// ── UTILS ──────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Error del servidor');
  return data;
}

function showAlert(id, msg, type = 'danger') {
  document.getElementById(id).innerHTML = `<div class="alert alert-${type}">${msg}</div>`;
}

function fmtCOP(n) {
  if (!n) return '$0';
  if (n >= 1000000) return `$${(n/1000000).toFixed(1)}M`;
  if (n >= 1000) return `$${Math.round(n/1000)}K`;
  return `$${Number(n).toLocaleString('es-CO')}`;
}

function estadoBadge(e) {
  return `<span class="badge badge-${e}">${e}</span>`;
}

// ── AUTH ───────────────────────────────────────────────────────────────────
async function login() {
  const email = document.getElementById('login-email').value.trim();
  if (!email) return showAlert('login-alert', 'Ingresa tu correo');
  try {
    const d = await api('/login', { method: 'POST', body: JSON.stringify({ email }) });
    iniciarSesion(d.viverista);
  } catch (e) {
    showAlert('login-alert', e.message);
  }
}

async function registro() {
  const nombre = document.getElementById('reg-nombre').value.trim();
  const email = document.getElementById('reg-email').value.trim();
  const telefono = document.getElementById('reg-telefono').value.trim();
  const municipio = document.getElementById('reg-municipio').value;
  const nombre_vivero = document.getElementById('reg-vivero').value.trim();
  if (!nombre || !email || !municipio || !nombre_vivero)
    return showAlert('reg-alert', 'Completa todos los campos obligatorios (*)');
  try {
    const d = await api('/registro', { method: 'POST', body: JSON.stringify({ nombre, email, telefono, municipio, nombre_vivero }) });
    iniciarSesion(d.viverista);
  } catch (e) {
    showAlert('reg-alert', e.message);
  }
}

function iniciarSesion(viverista) {
  state.viverista = viverista;
  document.getElementById('login-page').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  document.getElementById('nav-nombre').textContent = `👤 ${viverista.nombre.split(' ')[0]} · ${viverista.municipio}`;
  cargarCatalogo();
  cargarMarketplace();
}

function logout() {
  state = { viverista: null, archivoSeleccionado: null, ultimoAnalisis: null, chatHistory: [], todasLasPlantas: [] };
  document.getElementById('app').classList.add('hidden');
  document.getElementById('login-page').classList.remove('hidden');
}

// ── NAVIGATION ─────────────────────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById(`page-${name}`).classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  if (name === 'transacciones') cargarVentas();
}

function showTab(id, btn) {
  const parent = btn.closest('.page') || document.body;
  parent.querySelectorAll('[id^="tab-"]').forEach(t => t.classList.add('hidden'));
  parent.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.remove('hidden');
  btn.classList.add('active');
}

// ── CATÁLOGO ───────────────────────────────────────────────────────────────
async function cargarCatalogo() {
  try {
    const d = await api(`/catalogo/${state.viverista.id}`);
    const cont = document.getElementById('mis-plantas');
    if (!d.plantas.length) {
      cont.innerHTML = `<div class="text-center text-muted" style="padding:2rem;grid-column:1/-1">
        Tu catálogo está vacío. ¡Agrega tu primera planta con IA!
      </div>`;
      return;
    }
    cont.innerHTML = d.plantas.map(p => plantaCard(p, false)).join('');
  } catch (e) { console.error(e); }
}

function plantaCard(p, marketplace = true) {
  const img = p.imagen_url
    ? `<img src="${p.imagen_url}" alt="${p.nombre_comun}" loading="lazy">`
    : `<div class="img-placeholder">🌿</div>`;
  const acciones = marketplace ? `
    <div class="form-group" style="margin-top:.8rem">
      <input type="number" class="qty-input" id="qty-${p.id}" value="1" min="1" max="${p.stock_unidades}">
    </div>
    <button class="btn btn-primary btn-full btn-sm" onclick="pedirPlanta('${p.id}','${p.viverista_id}',${p.precio_cop})">
      🛒 Pedir ahora
    </button>` : '';
  return `
    <div class="plant-card">
      ${img}
      <div class="plant-card-body">
        <div class="plant-name">${p.nombre_comun}</div>
        ${p.nombre_cientifico ? `<div class="plant-sci">${p.nombre_cientifico}</div>` : ''}
        ${marketplace ? `<div class="plant-meta">🌱 ${p.nombre_vivero || ''} · 📍 ${p.municipio || ''}</div>` : ''}
        <div class="flex justify-between items-center">
          <span class="plant-price">${fmtCOP(p.precio_cop)} COP</span>
          <span class="plant-stock">${p.stock_unidades} uds</span>
        </div>
        ${p.descripcion ? `<p style="font-size:.8rem;color:var(--muted);margin-top:6px">${p.descripcion.substring(0,80)}...</p>` : ''}
        ${acciones}
      </div>
    </div>`;
}

// ── VISIÓN IA ──────────────────────────────────────────────────────────────
function onFileSelected(input) {
  const file = input.files[0];
  if (!file) return;
  state.archivoSeleccionado = file;
  const reader = new FileReader();
  reader.onload = e => {
    const img = document.getElementById('preview-img');
    img.src = e.target.result;
    img.classList.remove('hidden');
  };
  reader.readAsDataURL(file);
  document.getElementById('btn-analizar').disabled = false;
}

// Drag & drop
const zone = document.getElementById('upload-zone');
zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
zone.addEventListener('drop', e => {
  e.preventDefault(); zone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) { document.getElementById('file-input').files = e.dataTransfer.files; onFileSelected(document.getElementById('file-input')); }
});

async function analizarPlanta() {
  if (!state.archivoSeleccionado) return;
  const btn = document.getElementById('btn-analizar');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Analizando...';

  const fd = new FormData();
  fd.append('viverista_id', state.viverista.id);
  fd.append('municipio', state.viverista.municipio || 'Cajicá');
  fd.append('precio_cop', document.getElementById('precio-manual').value || '0');
  fd.append('stock', document.getElementById('stock-manual').value || '1');
  fd.append('imagen', state.archivoSeleccionado);

  try {
    const d = await fetch(API + '/analizar-planta', { method: 'POST', body: fd }).then(r => r.json());
    if (d.ok) {
      state.ultimoAnalisis = d;
      mostrarAnalisis(d.analisis);
      document.getElementById('resultado-analisis').style.display = 'block';
    } else {
      alert('Error al analizar la imagen. Intenta con otra foto.');
    }
  } catch (e) {
    alert('Error de conexión: ' + e.message);
  }

  btn.disabled = false;
  btn.innerHTML = '🤖 Analizar con IA';
}

function mostrarAnalisis(a) {
  document.getElementById('analisis-content').innerHTML = `
    <div class="kpi-grid" style="margin-bottom:1rem">
      <div class="kpi-card"><div class="kpi-value" style="font-size:1.1rem">${a.nombre_comun}</div><div class="kpi-label">Nombre común</div></div>
      <div class="kpi-card"><div class="kpi-value" style="font-size:1rem">${fmtCOP(a.precio_estimado_cop)}</div><div class="kpi-label">Precio sugerido</div></div>
      <div class="kpi-card"><div class="kpi-value" style="font-size:1rem">${Math.round((a.confianza||0)*100)}%</div><div class="kpi-label">Confianza IA</div></div>
    </div>
    ${a.nombre_cientifico ? `<p style="font-style:italic;color:var(--muted);margin-bottom:.5rem">${a.nombre_cientifico}</p>` : ''}
    ${a.descripcion ? `<p style="margin-bottom:.5rem;font-size:.9rem">${a.descripcion}</p>` : ''}
    ${a.cuidados ? `<div class="alert alert-info" style="font-size:.85rem">💧 ${a.cuidados}</div>` : ''}
    ${a.advertencias && a.advertencias !== 'null' ? `<div class="alert alert-danger" style="font-size:.85rem">⚠️ ${a.advertencias}</div>` : ''}
  `;
}

function confirmarPlanta() {
  if (state.ultimoAnalisis?.planta_id) {
    document.getElementById('resultado-analisis').style.display = 'none';
    showTab('tab-ver', document.querySelector('.tab'));
    cargarCatalogo();
    alert(`✅ ¡${state.ultimoAnalisis.analisis.nombre_comun} agregada a tu catálogo!`);
  }
}

// ── MARKETPLACE ───────────────────────────────────────────────────────────
async function cargarMarketplace() {
  try {
    const d = await api(`/marketplace?viverista_id=${state.viverista.id}`);
    state.todasLasPlantas = d.plantas;
    renderMarketplace(d.plantas);
  } catch (e) { console.error(e); }
}

function renderMarketplace(plantas) {
  const cont = document.getElementById('marketplace-plantas');
  if (!plantas.length) {
    cont.innerHTML = `<div class="text-center text-muted" style="padding:2rem;grid-column:1/-1">No hay plantas disponibles con esos filtros.</div>`;
    return;
  }
  cont.innerHTML = plantas.map(p => plantaCard(p, true)).join('');
}

function filtrarMarketplace() {
  const buscar = document.getElementById('buscar-input').value.toLowerCase();
  const municipio = document.getElementById('municipio-filter').value;
  let resultado = state.todasLasPlantas;
  if (buscar) resultado = resultado.filter(p =>
    `${p.nombre_comun} ${p.nombre_cientifico} ${p.descripcion}`.toLowerCase().includes(buscar));
  if (municipio) resultado = resultado.filter(p => p.municipio === municipio);
  renderMarketplace(resultado);
}

async function pedirPlanta(planta_id, viverista_id, precio) {
  const cantidad = parseInt(document.getElementById(`qty-${planta_id}`).value) || 1;
  if (!confirm(`¿Confirmas el pedido de ${cantidad} unidad(es) por ${fmtCOP(cantidad * precio)} COP?`)) return;
  try {
    await api('/transaccion', { method: 'POST', body: JSON.stringify({
      viverista_id, comprador_id: state.viverista.id,
      planta_id, cantidad, precio_unitario: precio,
    })});
    alert(`✅ Pedido enviado. El viverista recibirá tu solicitud.`);
    cargarMarketplace();
  } catch (e) {
    alert('Error al crear el pedido: ' + e.message);
  }
}

// ── TRANSACCIONES ──────────────────────────────────────────────────────────
async function cargarVentas() {
  try {
    const d = await api(`/transacciones/${state.viverista.id}?tipo=ventas`);
    const cont = document.getElementById('ventas-content');
    if (!d.transacciones.length) {
      cont.innerHTML = '<p class="text-center text-muted" style="padding:2rem">Aún no tienes ventas.</p>';
      return;
    }
    cont.innerHTML = d.transacciones.map(txnRow).join('');
  } catch (e) { console.error(e); }
}

async function cargarCompras() {
  try {
    const d = await api(`/transacciones/${state.viverista.id}?tipo=compras`);
    const cont = document.getElementById('compras-content');
    if (!d.transacciones.length) {
      cont.innerHTML = '<p class="text-center text-muted" style="padding:2rem">Aún no has realizado compras.</p>';
      return;
    }
    cont.innerHTML = d.transacciones.map(txnRow).join('');
  } catch (e) { console.error(e); }
}

function txnRow(t) {
  return `<div class="txn-row">
    <div><strong>${t.nombre_planta}</strong><br><span class="text-muted" style="font-size:.8rem">${t.contraparte_nombre}</span></div>
    <div>${t.cantidad} uds × ${fmtCOP(t.precio_unitario_cop)}</div>
    <div style="font-weight:700;color:var(--green)">${fmtCOP(t.total_cop)} COP</div>
    <div>${estadoBadge(t.estado)}</div>
    <div style="font-size:.8rem;color:var(--muted)">${(t.created_at||'').substring(0,10)}</div>
  </div>`;
}

// ── CHAT ────────────────────────────────────────────────────────────────────
async function enviarChat() {
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  agregarMensaje(msg, 'user');
  state.chatHistory.push(msg);

  const typing = agregarMensaje('...', 'bot');
  try {
    const d = await api('/chat', { method: 'POST', body: JSON.stringify({
      mensaje: msg,
      municipio: state.viverista?.municipio || 'Cajicá',
      historial: state.chatHistory.slice(-6),
    })});
    typing.textContent = d.respuesta;
    state.chatHistory.push(d.respuesta);
  } catch (e) {
    typing.textContent = 'Error de conexión. Intenta de nuevo.';
  }
}

function agregarMensaje(texto, rol) {
  const box = document.getElementById('chat-box');
  const div = document.createElement('div');
  div.className = `msg msg-${rol}`;
  div.textContent = texto;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

function preguntaRapida(q) {
  document.getElementById('chat-input').value = q;
  enviarChat();
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def frontend():
    return HTMLResponse(content=HTML_PAGE, status_code=200)

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
    if imagen.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Formato no soportado. Usa JPEG, PNG o WebP.")
    imagen_bytes = await imagen.read()
    resultado = await analizar_planta_pipeline(imagen_bytes, municipio)
    if not resultado:
        raise HTTPException(500, "No se pudo analizar la imagen.")
    try:
        img = Image.open(BytesIO(imagen_bytes))
        if img.width > 800 or img.height > 800:
            img.thumbnail((800, 800), Image.LANCZOS)
        buf = BytesIO()
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
    })
    return {"ok": True, "analisis": resultado, "planta_id": planta["id"] if planta else None}

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
    from whatsapp import procesar_whatsapp
    return await procesar_whatsapp(
        From=From, Body=Body,
        NumMedia=NumMedia,
        MediaUrl0=MediaUrl0,
        MediaContentType0=MediaContentType0,
    )


# ─── CATCH-ALL FRONTEND (DEBE IR AL FINAL) ────────────────────────────────────
@app.get("/{path:path}", response_class=HTMLResponse)
async def frontend_catch(path: str):
    return HTMLResponse(content=HTML_PAGE, status_code=200)
