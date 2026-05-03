# 🌿 ViveroOnline

**Marketplace B2B AgTech para la Sabana de Bogotá** — conecta viveristas con compradores (paisajistas, constructoras, conjuntos residenciales) usando IA para identificación, precios sugeridos y predicción de demanda.

---

## 🏗️ Arquitectura

```
Usuario WhatsApp/Web ──► FastAPI (Vercel Python)
                            │
                            ├── Auth: Twilio Verify OTP → Supabase Auth
                            ├── Catálogo: Gemini Vision + Storage
                            ├── Marketplace: PostGIS búsqueda geo
                            ├── Chat: LangGraph router → 8 agentes
                            │    (CEO · Researcher · Architect · StartupBuilder ·
                            │     PlantIdentifier · InventoryBuilder ·
                            │     LandscapeAdvisor · DemandPredictor)
                            └── KPIs: vistas del Data Flywheel
                            │
                            ▼
Supabase (São Paulo) · PostgreSQL 17 + PostGIS + pgvector
  - 12 tablas con RLS por rol (admin · viverista · comprador)
  - Hook JWT custom (inyecta rol + whatsapp_numero)
  - 3 vistas flywheel (v_flywheel_kpis · v_crecimiento_semanal · v_top_viveristas)
  - 3 buckets Storage (plantas-fotos · viveros-fotos · documentos-privados)
```

## 🔌 Endpoints API

### Auth
- `POST /api/auth/otp/send` — envía OTP por WhatsApp
- `POST /api/auth/otp/verify` — valida código → retorna access/refresh tokens
- `POST /api/auth/onboarding` — crea perfil + (vivero | cliente)
- `GET  /api/auth/me` — contexto del usuario autenticado

### Catálogo (viverista)
- `GET  /api/catalogo`
- `POST /api/catalogo/identificar` — sube foto → Gemini Vision
- `POST /api/catalogo/guardar`

### Marketplace (comprador)
- `GET  /api/marketplace?q=&municipio=&radio_km=` — búsqueda geo
- `GET  /api/marketplace/item/{id}`
- `POST /api/marketplace/cotizacion`

### Transacciones / Chat / KPIs
- `GET  /api/transacciones` — ventas (viverista) o compras (comprador)
- `POST /api/chat` — LangGraph router → agente
- `GET  /api/kpis` — solo admin

---

## 🚀 Setup local

```bash
git clone https://github.com/viveroonlinecomco-code/vivero-online.git
cd vivero-online
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # editar con credenciales
uvicorn app.main:app --reload --port 8000
```

Abre http://localhost:8000

---

## 🔐 Credenciales requeridas

| Servicio | Para qué | Dónde obtener |
|----------|----------|---------------|
| **Supabase** | BD + Auth + Storage | Dashboard → Settings → API |
| **Twilio Verify** | OTP por WhatsApp | Console → Verify → Services |
| **Gemini** | Vision + chat + embeddings | aistudio.google.com/app/apikey |

### Twilio: configurar servicio Verify

1. Console Twilio → Verify → Services → Create Service
2. Nombre: `ViveroOnline`
3. Agrega canal WhatsApp
4. En desarrollo puedes usar el **sandbox WhatsApp**: los usuarios envían `join xxx-xxx` al `+14155238886`
5. Copia el **Verify Service SID** (empieza con `VA`) a `TWILIO_VERIFY_SERVICE_SID`

---

## ☁️ Deploy a Vercel

```bash
npm i -g vercel
vercel login
vercel link    # conecta a tu proyecto "vivero-online-ia"
vercel --prod
```

### Variables de entorno en Vercel

`Settings → Environment Variables`:

```
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_KEY          (secret)
SUPABASE_JWT_SECRET           (secret)
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN             (secret)
TWILIO_VERIFY_SERVICE_SID
TWILIO_WHATSAPP_FROM
GEMINI_API_KEY                (secret)
GEMINI_MODEL=gemini-2.5-flash
APP_BASE_URL=https://vivero-online-j3gi.vercel.app
ENV=production
```

---

## 🧪 Probar el flujo

1. Abre `/` → landing
2. Click "Viveros" → `/auth/ingresar`
3. Ingresa `+57 3001234567`
4. OTP en WhatsApp → 6 dígitos
5. **Primera vez:** onboarding con rol viverista/comprador
6. Redirige al dashboard

### Chat IA

```bash
curl -X POST https://tu-app.vercel.app/api/chat \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"mensaje": "¿Qué plantas son más rentables en lluvia?", "historial": []}'
```

---

## 🛡️ Seguridad y cumplimiento

- **RLS en todas las tablas** con políticas optimizadas `(SELECT auth.jwt())`
- **Habeas Data (Ley 1581)** en onboarding + página pública
- **JWT firmado** con `SUPABASE_JWT_SECRET`
- **Extensiones aisladas** fuera de `public` (pg_trgm, unaccent)

---

## 📊 Data Flywheel (para inversores)

```sql
SELECT * FROM v_flywheel_kpis;
SELECT * FROM v_crecimiento_semanal;
SELECT * FROM v_top_viveristas;
```

Se consumen en `/admin` vía `GET /api/kpis`.

---

## 🔜 Roadmap

- Wompi pagos (PSE + tarjetas)
- YOLO-11 preprocessing antes de Gemini Vision
- Webhook Twilio → respuesta del agente por WhatsApp
- Dashboard inversor público (datos anonimizados)
- PWA con push

---

© 2026 · ViveroOnline.com.co
