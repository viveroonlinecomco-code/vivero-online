# 🚀 Guía de Deploy - ViveroOnline

Esta guía te lleva desde el repo local hasta producción en Vercel, paso a paso.

---

## ✅ Prerequisitos

- Cuenta Supabase activa (proyecto `qhjditntqtjiljeftqun` ya existe)
- Cuenta Vercel activa (proyecto `vivero-online-ia` ya existe)
- Cuenta Twilio con número verificado
- API Key de Gemini
- Git CLI + Node.js + Python 3.12

---

## 1️⃣ Reemplazar el código del repo GitHub

### Opción A: Sobrescribir sobre el repo existente

```bash
cd ~/proyectos  # o donde quieras
git clone https://github.com/viveroonlinecomco-code/vivero-online.git
cd vivero-online

# Haz backup del código legacy
git checkout -b legacy-backup
git push origin legacy-backup

# Vuelve a main y borra todo excepto .git
git checkout main
find . -mindepth 1 -not -path "./.git*" -delete

# Copia el nuevo código (ajusta la ruta al zip que te entrego)
unzip -d . ~/Downloads/vivero-online-nuevo.zip
cp -r vivero-online/* vivero-online/.[!.]* .
rm -rf vivero-online

git add .
git commit -m "feat: rearquitectura completa - FastAPI + LangGraph + 8 agentes + Stitch frontend"
git push origin main
```

Vercel detecta el push y dispara deploy automático.

### Opción B: Deploy directo via Vercel CLI

```bash
cd ~/proyectos/vivero-online
npm i -g vercel
vercel login
vercel link  # elige "Elena's projects / vivero-online-ia"
vercel --prod
```

---

## 2️⃣ Configurar variables de entorno en Vercel

**Dashboard Vercel → vivero-online-ia → Settings → Environment Variables**

Copia estas variables (con los valores reales de tu cuenta):

### 🗄️ Supabase (ya lo tienes)

```
SUPABASE_URL = https://qhjditntqtjiljeftqun.supabase.co
SUPABASE_ANON_KEY = eyJhbGc...      (Supabase Dashboard → Settings → API → anon public)
SUPABASE_SERVICE_KEY = eyJhbGc...   (Settings → API → service_role)  ⚠️ secret
SUPABASE_JWT_SECRET = ...           (Settings → API → JWT Secret)    ⚠️ secret
```

### 📱 Twilio (crea si no tienes)

1. Ve a [console.twilio.com](https://console.twilio.com)
2. Messaging → **Try it out → WhatsApp Sandbox** (o activa WhatsApp Business si ya lo tienes)
3. Verify → Services → **Create Service**:
   - Nombre: `ViveroOnline`
   - Canal: WhatsApp activado
4. Copia estos valores:

```
TWILIO_ACCOUNT_SID = AC...
TWILIO_AUTH_TOKEN = ...             ⚠️ secret
TWILIO_VERIFY_SERVICE_SID = VA...
TWILIO_WHATSAPP_FROM = whatsapp:+14155238886   # sandbox; si tienes BA usa tu número
```

**⚠️ Importante para sandbox:** cada número que quiera recibir OTP debe enviar primero `join <código>` al `+14155238886` desde su WhatsApp. Los usuarios en producción (BA aprobada) no tienen este paso.

### 🤖 Gemini

1. Ve a [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Create API Key → copia

```
GEMINI_API_KEY = AIza...            ⚠️ secret
GEMINI_MODEL = gemini-2.5-flash
GEMINI_EMBEDDING_MODEL = text-embedding-004
```

### 🌐 App

```
APP_BASE_URL = https://vivero-online-j3gi.vercel.app
ENV = production
```

---

## 3️⃣ Verificar que el deploy funciona

Después de que Vercel termine el build:

```bash
# Health check
curl https://vivero-online-j3gi.vercel.app/api/health
# Respuesta esperada:
# {"ok":true,"service":"vivero-online","version":"0.1.0","env":"production"}
```

### Flujo completo de prueba

1. **Landing**: abre `https://vivero-online-j3gi.vercel.app/`
2. Click "Viveros" → debe ir a `/auth/ingresar`
3. **Primer requisito:** envía `join <código>` al `+14155238886` desde WhatsApp (solo sandbox)
4. En el formulario: `+57 3001234567` (tu número real) → "Enviar código"
5. Recibes OTP en WhatsApp → ingresas los 6 dígitos
6. Primera vez: `/auth/onboarding` → eliges **Viverista**
7. Rellenas datos → "Finalizar registro"
8. Redirige a `/viverista` con tu dashboard vacío

---

## 4️⃣ Troubleshooting

### "Twilio error: 60200" al enviar OTP
- Número no está en sandbox → pídele que envíe `join <código>` al `+14155238886`
- O pasa a Twilio WhatsApp Business API (requiere verificación Meta)

### "Internal Server Error 500" en `/api/auth/otp/verify`
- Revisa logs: `vercel logs --follow`
- Casi siempre es un env var faltante

### Frontend pinta "$NaN" o "undefined"
- El usuario no tiene inventario aún — es el estado esperado al inicio

### CORS error en el browser
- Revisa que `APP_BASE_URL` en Vercel esté correcto y sea exactamente el dominio que abres
- El CORS también acepta `localhost:3000` y `localhost:8000` para dev

### El chat IA responde "No encontré información"
- Normal en BD vacía. Los agentes de datos (Researcher, Inventory, Demand) necesitan transacciones reales para contestar con cifras.

---

## 5️⃣ Post-deploy: tareas manuales

### 5.1. Crear el primer usuario admin

Por diseño, **nadie se registra como admin** via onboarding. Hay que elevarlo manualmente:

```sql
-- En Supabase SQL Editor, con el user_id del admin (Dashboard → Auth → Users)
UPDATE public.perfiles
SET rol = 'admin'
WHERE id = 'TU-USER-ID-UUID';
```

Después de logout/login el JWT traerá `rol=admin`.

### 5.2. Semillas iniciales (opcional)

Si quieres que el marketplace no esté vacío en la primera demo, puedes cargar 5-10 plantas comunes y 2-3 viveros de prueba via el dashboard del viverista (subiendo fotos reales → Gemini identifica y guarda).

### 5.3. Conectar dominio custom

En Vercel → `vivero-online-ia` → Settings → Domains → Add:
- `vivero-online.com.co`
- `www.vivero-online.com.co`

Sigue las instrucciones de DNS (Vercel te da los registros exactos).

### 5.4. Webhook Twilio entrante (futuro)

Cuando quieras que los viveristas respondan al agente **por WhatsApp** (no solo desde la web):

1. Vercel → Add function: `POST /api/whatsapp/webhook`
2. Twilio → WhatsApp Sender → A Message Comes In → `https://tu-app/api/whatsapp/webhook`
3. El handler ya está esbozado en el plan (roadmap)

---

## 📋 Checklist post-deploy

- [ ] `/api/health` responde OK
- [ ] `/` (landing) renderiza
- [ ] `/auth/ingresar` renderiza el formulario con `+57`
- [ ] OTP llega al WhatsApp del tester
- [ ] Onboarding crea `perfiles` + `viveros` (SELECT * FROM perfiles te lo muestra)
- [ ] `/viverista` carga sin errores
- [ ] `/viverista/identificar` sube foto y Gemini responde
- [ ] `/marketplace` carga (vacío si no hay items)
- [ ] `/api/kpis` responde 403 si no eres admin, 200 si lo eres

---

## 📞 Soporte

Si algo falla, copia el error exacto + el endpoint que lo disparó y lo debuggeamos juntos.

© 2026 ViveroOnline.com.co
