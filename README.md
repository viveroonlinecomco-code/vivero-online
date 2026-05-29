# 🌿 ViveroOnline

**Marketplace B2B AgTech para la Sabana de Bogotá** — conecta viveristas con compradores B2B (paisajistas, constructoras, conjuntos residenciales, instituciones) usando IA para identificación de plantas, precios sugeridos y descubrimiento de demanda en SECOP.

🌐 **Producción:** https://vivero-online-j3gi.vercel.app

---

## 🏗️ Arquitectura

```Usuario (web) ──► FastAPI (Vercel Python 3.12)
│
├── Auth: Supabase Email OTP (Gmail SMTP gratis)
├── Catálogo: Gemini Vision + Supabase Storage
├── Marketplace: PostGIS búsqueda geo
├── Chat: LangGraph router → agentes IA
├── SECOP: ingesta + clasificación con Gemini
├── Suscripciones: plan Inteligencia + ePayco (stub)
└── Admin: KPIs + flywheel
│
▼
Supabase (São Paulo)
PostgreSQL 17 + PostGIS + pgvector
- RLS por rol (admin · viverista · comprador)
- Hook JWT custom (inyecta rol desde perfiles)
- Vistas flywheel + métricas públicas
- Storage buckets (fotos plantas + viveros)

---

## 👤 Roles y permisos

| Rol | Qué puede hacer |
|-----|-----------------|
| **admin** | Ve todo: panel admin, KPIs, ingesta SECOP. Puede acceder a flujos viverista y comprador también |
| **viverista** | Cargar inventario (con IA), editar perfil del vivero, ver suscripción |
| **comprador** | Marketplace, cotizaciones multi-proyecto, contacto directo con viveristas |
| **anon** (sin login) | Solo vitrina pública del marketplace: foto, nombre vivero, ciudad, historia. **No ve teléfono, WhatsApp, dirección ni coordenadas.** |

---

## 🔐 Login (sistema Email OTP, operando a $0)

1. Usuario ingresa su email en `/auth/ingresar`
2. Supabase manda un código de 6 dígitos vía **Gmail SMTP** (gratis, ~500/día)
3. Usuario ingresa código → recibe sesión (access + refresh token)
4. **Primera vez**: pasa al onboarding (rol viverista o comprador + WhatsApp de contacto)
5. **Después**: va directo a su dashboard según rol

> 💡 **No hay Twilio.** El login es 100% Supabase + Gmail SMTP custom. Costo: $0.

---

## 🔌 Endpoints API

### Auth (público)
- `POST /api/auth/otp/send` — envía OTP por email
- `POST /api/auth/otp/verify` — valida código → tokens
- `POST /api/auth/onboarding` — crea perfil + (vivero | cliente) + WhatsApp
- `GET  /api/auth/me` — contexto del usuario (DB > JWT)

### Marketplace público (sin login)
- `GET  /api/public/marketplace` — vitrina pública (lista)
- `GET  /api/public/marketplace/item/{id}` — detalle público
- `GET  /api/public/stats` — métricas para landing
- `GET  /api/public/health`

### Marketplace autenticado (comprador)
- `GET  /api/marketplace?q=&municipio=&radio_km=` — búsqueda geo completa
- `GET  /api/marketplace/item/{id}` — detalle con datos de contacto del vivero
- `POST /api/marketplace/cotizacion` — cotizaciones multi-proyecto
- `GET  /api/marketplace/cotizacion/proyectos` — lista de proyectos del comprador

### Viverista
- `GET  /api/catalogo` — inventario propio
- `POST /api/catalogo/identificar` — sube foto → Gemini Vision
- `POST /api/catalogo/guardar`
- `GET  /api/viveros/me` — perfil del vivero propio
- `PATCH /api/viveros/me` — actualizar perfil

### Suscripciones (plan Inteligencia)
- `GET  /api/suscripcion/estado` — suscripción activa del usuario
- `POST /api/suscripcion/iniciar-pago` — checkout ePayco (stub: 503 hasta tener credenciales)
- `POST /api/pagos/webhook/epayco` — webhook de confirmación

### Inteligencia (admin + suscriptos)
- `GET  /mi-cuenta/inteligencia` — dashboard mercado + SECOP unificado
- Admin tiene acceso sin suscripción

### Chat / KPIs
- `POST /api/chat` — LangGraph router → agente IA
- `GET  /api/kpis` — solo admin

---

## 🛡️ Seguridad y privacidad

### Modelo de privacidad de viveros

| Columna | Marketplace público | Usuario logueado |
|---------|:---:|:---:|
| `nombre_vivero`, `ciudad`, `departamento`, `foto_url`, `fotos_galeria`, `historia` | ✅ Visible | ✅ |
| `propietario`, `telefono`, `whatsapp_numero`, `nit`, `direccion`, `latitud`, `longitud` | ❌ Oculto | ✅ |

### Capas de defensa
- **RLS habilitado** en todas las tablas con PII (`clientes`, `pagos`, `suscripciones`, `transacciones_b2b`, `perfiles`, `conversations_memory`, `cotizaciones`, `viveros`)
- **Tabla `viveros` restringida a `authenticated`** — la anon key no puede leer viveros directo (defensa contra scrapers)
- **Hook `custom_access_token_hook`** inyecta rol desde tabla `perfiles` al JWT
- **Endpoint `/api/auth/me` patrón "DB > JWT"** — la BD es source-of-truth para rol
- **Habeas Data (Ley 1581)** obligatorio en onboarding
- **HTTP headers** vía `vercel.json`: HSTS (max-age 2 años + preload), X-Content-Type-Options nosniff, X-Frame-Options DENY, Referrer-Policy strict-origin-when-cross-origin, CSP estricto
- **CORS restrictivo**: solo dominios propios + preview de Vercel
- **Funciones peligrosas** (`rls_auto_enable`, `sync_perfil_to_auth_metadata`) revocadas de anon/authenticated

---

## 💰 Costo del stack (operación a $0)

| Servicio | Plan | Costo | Notas |
|----------|------|-------|-------|
| **Vercel** | Hobby | $0 | Para uso comercial real → Pro ($20/mes) |
| **Supabase** | Free | $0 | Pausa tras 7 días sin actividad; Pro ($25/mes) habilita Leaked Password Protection |
| **Gmail SMTP** | Personal | $0 | ~500 emails/día con App Password |
| **Gemini** | Free tier | $0 | Con límites de rate |
| **ePayco** | Pendiente | — | Activar cuando lleguen credenciales |
| **Bot WhatsApp** | Pausado | — | Activar con Meta WABA (1000 conv/mes gratis) o Twilio con saldo |

**Total mientras validamos: $0/mes** 🌱

---

## 🚀 Setup local (para desarrolladores futuros)

```bashgit clone https://github.com/viveroonlinecomco-code/vivero-online.git
cd vivero-online
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # editar con credenciales
uvicorn app.main:app --reload --port 8000

Abre http://localhost:8000

---

## 🔧 Variables de entorno

```envSupabase
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_KEY          # (secret)
SUPABASE_JWT_SECRET           # (secret)Gemini
GEMINI_API_KEY                # (secret)
GEMINI_MODEL=gemini-2.5-flash
GEMINI_EMBEDDING_MODEL=text-embedding-004ePayco (pendiente activar)
EPAYCO_PUBLIC_KEY
EPAYCO_PRIVATE_KEY            # (secret)
EPAYCO_P_CUST_ID              # (secret)
EPAYCO_P_KEY                  # (secret)App
APP_BASE_URL=https://vivero-online-j3gi.vercel.app
ENV=production

> 📧 **Email OTP**: se configura en el dashboard de Supabase (Authentication → SMTP → Custom). No requiere env vars.

---

## ☁️ Deploy a Vercel

Está en producción auto-deployando desde `main`. Cada push a main → deploy a `vivero-online-j3gi.vercel.app`.

Para forzar un redeploy manual: Vercel dashboard → Deployments → "Redeploy".

---

## 📊 Data Flywheel

Vistas SQL para métricas internas:

```sqlSELECT * FROM v_public_flywheel;        -- métricas públicas seguras
SELECT * FROM v_flywheel_kpis;          -- KPIs internos
SELECT * FROM v_crecimiento_semanal;
SELECT * FROM v_top_viveristas;

---

## ⚠️ Limitaciones conocidas

- **Bot WhatsApp**: no está en producción. Twilio sandbox (`+14155238886`) funcionaba para pruebas pero requiere "join code" por usuario; sandbox suspendido por falta de saldo. Pendiente decidir Meta WABA vs Twilio producción.
- **ePayco**: endpoints implementados pero devuelven 503 hasta que se carguen las 4 credenciales en Vercel.
- **Supabase Free**: el proyecto se pausa tras 7 días sin actividad. Reactivar manualmente.
- **Vercel Hobby**: técnicamente no permite uso comercial. Migrar a Pro cuando se empiece a cobrar suscripciones en serio.
- **Gmail SMTP**: ~500 emails/día. Si crece el tráfico → migrar a Resend (3.000/mes gratis) con dominio propio.

---

## 🔜 Roadmap

### Próximos
- **Rate limiting** con `slowapi` en endpoints de auth y pagos
- **Defensa permanente en `deps.py`**: hacer que `require_*` SIEMPRE consulte DB para el rol (no solo cuando JWT no lo trae)
- **Activar ePayco real** (cargar las 4 env vars)
- **Botón "Pagar" en marketplace** para transacciones B2B reales

### Mediano plazo
- **Bot WhatsApp en producción** (Meta WABA gratis hasta 1.000 conv/mes, o Twilio con saldo)
- **Dominio propio** `viveroonline.com.co` → migrar Gmail SMTP a Resend
- **Plan Pro Supabase** ($25/mes) cuando haya 50+ usuarios activos
- **YOLO-11 preprocessing** antes de Gemini Vision para mejorar identificación
- **Webhook WhatsApp → respuesta del agente** (cuando el bot esté en producción)

### Visión
- Dashboard inversor público con datos anonimizados
- PWA con push notifications
- Expansión geográfica más allá de Sabana de Bogotá

---

## 🗂️ Estructura del proyectovivero-online/
├── api/                  # Entrypoints Vercel (agent, db, graph, whatsapp, yolo)
├── app/
│   ├── agents/           # Agentes LangGraph
│   ├── auth/             # deps.py (UserContext, require_*) + flow.py (OTP)
│   ├── routes/           # Endpoints FastAPI
│   ├── schemas/          # Pydantic models
│   ├── services/         # Supabase, Twilio (legacy), helpers
│   ├── static/js/        # vivero.js (frontend helper)
│   ├── templates/        # Páginas HTML servidas con _tpl()
│   ├── config.py         # Settings
│   └── main.py           # FastAPI app + CORS
├── docs/
├── README.md
├── requirements.txt
└── vercel.json           # Headers de seguridad

---

## 🆔 Datos clave

- **Repo:** github.com/viveroonlinecomco-code/vivero-online (privado)
- **Vercel project ID:** `prj_c5t4MOAX91VmbVQROVNqBvKn0Sm6`
- **Supabase project ID:** `qhjditntqtjiljeftqun`
- **Runtime:** Python 3.12 en Vercel

---

© 2026 · ViveroOnline.com.co · Sabana de Bogotá
