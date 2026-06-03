# ViveroOnline.com.co 🌿
**Marketplace B2B de plantas ornamentales — Sabana de Bogotá, Colombia**

> Plataforma AgTech que conecta viveros de Cundinamarca con compradores institucionales (paisajistas, constructoras, conjuntos residenciales) usando inteligencia artificial, automatización y pagos integrados.

---

## 🌐 Producción

| Recurso | URL |
|---------|-----|
| App | https://app.viveroonline.com.co |
| Supabase | https://supabase.com/dashboard/project/qhjditntqtjiljeftqun |
| Vercel | https://vercel.com/elenas-projects-0d05ec06/vivero-online-ia |
| Repo | github.com/viveroonlinecomco-code/vivero-online (privado) |

---

## 🏗️ Stack — $0/mes en producción

| Capa | Tecnología | Costo |
|------|-----------|-------|
| Backend | FastAPI + Python (serverless) | Vercel Free |
| Base de datos | Supabase PostgreSQL + Auth + Storage | Free tier |
| Frontend | HTML + Tailwind CSS | — |
| IA identificación | YOLO + Google Gemini | Free tier |
| WhatsApp bot | Meta Cloud API | $0 |
| Pagos | ePayco (sandbox → producción) | 2.99% por txn |
| Hosting | Vercel | Free |

---

## 👥 Roles del sistema

| Rol | Acceso | Dashboard |
|-----|--------|-----------|
| `viverista` | Gestiona inventario, aprueba/rechaza pedidos, edita perfil del vivero | `/viverista` |
| `comprador` | Explora marketplace, crea proyectos, cotiza y paga | `/comprador` |
| `admin` | Control total del marketplace | `/admin` |

---

## 🔐 Auth — Email OTP

Flujo completo sin contraseñas:

```
/auth/registro (elige rol)
  → /auth/ingresar?rol=X (email)
  → /auth/otp (código 6 dígitos)
  → /auth/onboarding (datos del perfil)
  → dashboard según rol
```

- Sin contraseñas — solo OTP por email (Supabase Auth)
- El rol se elige **antes** del email para personalizar la experiencia
- Onboarding diferenciado: viverista 🌿 vs comprador 🛒

---

## 💰 Modelo de precios — Markup 18%

```
Viverista ingresa precio base:    $10.000 COP
Comprador ve en marketplace:      $11.800 COP  (× 1.18)
Comprador paga:                   $11.800 COP
Viverista recibe:                 $10.000 COP  (su precio íntegro)
ViveroOnline retiene:             $ 1.800 COP  (18% markup)
```

- El agente IA recomienda precios basados en investigación de mercado
- El markup se suma automáticamente al precio base del viverista
- El viverista siempre recibe su precio base completo

---

## 🛒 Flujo de compra completo

```
1. Comprador explora marketplace → agrega plantas al carrito
2. Comprador crea proyecto (cotización en borrador)
3. Comprador → "Enviar al vivero" → estado: enviada
4. Viverista revisa en su dashboard → "Aprobar" o "Rechazar"
5. Si aprobada → comprador recibe WhatsApp con notificación
6. Comprador → "Pagar ahora" → /checkout/{cotizacion_id}
7. ePayco procesa el pago (precio base × 1.18)
8. /pagos/resultado → polling de estado → ✅ Aprobado
```

**Estados de una cotización:**
`borrador → enviada → aceptada → convertida (pagada)`
`enviada → rechazada`

---

## 🔒 Privacidad del marketplace

| Dato | Anon | Logueado | Con Plan Inteligencia |
|------|:----:|:--------:|:--------------------:|
| Nombre, ciudad, foto, historia | ✅ | ✅ | ✅ |
| Dirección exacta + mapa | ❌ | 🔒 | ✅ |
| Teléfono / WhatsApp vivero | ❌ | ❌ | ❌ (nunca) |

---

## ⭐ Plan Inteligencia (suscripción)

- Acceso a la **ubicación exacta** y mapa de los viveros
- Precio: por definir (actualmente activación manual por admin)
- Gestión desde el panel admin: activar / extender / cancelar en 1 click
- Activación manual en BD: tabla `suscripciones` (estado=activa, plan=inteligencia)

---

## 📱 Bot WhatsApp — Meta Cloud API

- **App Meta:** `viveroonline` (App ID: 1416485466524999)
- **Número de prueba:** +1 (555) 179-0423
- **Webhook:** `https://app.viveroonline.com.co/api/whatsapp/webhook`
- **Verify Token:** `viveroonline2026bot`

**Notificaciones automáticas:**
- Al viverista: nueva cotización enviada para aprobar
- Al comprador: cotización aprobada (con total a pagar × 1.18)
- Al comprador: cotización rechazada (con motivo)

**Pendiente para producción:**
- Conseguir SIM colombiana → registrar número real en Meta
- Publicar la app Meta (verificación del negocio)

---

## 🤖 Agentes IA

| Agente | Función |
|--------|---------|
| `inventory_builder` | Identifica plantas con YOLO + Gemini, sugiere precios, arma ficha del catálogo |
| `CEO_Orchestrator` | Analiza datos del flywheel, genera insights estratégicos (próximamente) |
| Bot WhatsApp | Responde consultas, gestiona inventario por chat |

---

## 📋 Panel Admin — `/admin`

6 secciones para gestión sin SQL:

| Sección | Funciones |
|---------|-----------|
| 📊 **Centro de Control** | KPIs tiempo real, top viveristas, accesos rápidos |
| 👥 **Comunidad** | Ver/buscar todos los usuarios, activar/suspender viveros |
| 📦 **Catálogo** | Todos los productos, cambiar disponibilidad |
| ⭐ **Suscripciones** | Dar/extender/cancelar Plan Inteligencia en 1 click |
| 📋 **SECOP II** | Ingestar y analizar procesos públicos de contratación |
| 🛡️ **Auditoría** | Log de actividad del sistema IA |

---

## 💳 Pagos — ePayco

| Variable Vercel | Descripción |
|----------------|-------------|
| `EPAYCO_P_CUST_ID_CLIENTE` | Public key |
| `EPAYCO_P_KEY` | Private key |
| `EPAYCO_PUBLIC_KEY` | API key |
| `EPAYCO_SECRET_KEY` | Webhook secret |

**Actualmente en sandbox.** Tarjeta de prueba:
- Visa: `4575623182290326` · CVV: `123` · Vence: `12/27`

**Para activar producción:** cargar las 4 credenciales reales en Vercel → Environment Variables.

---

## 🔧 Variables de entorno (Vercel)

```env
# App
APP_BASE_URL=https://app.viveroonline.com.co
ENV=production

# Supabase
SUPABASE_URL=https://qhjditntqtjiljeftqun.supabase.co
SUPABASE_SERVICE_ROLE_KEY=***

# WhatsApp Meta Cloud API
META_WA_PHONE_NUMBER_ID=975244915670534
META_WA_BUSINESS_ACCOUNT_ID=1401981671422232
META_WA_ACCESS_TOKEN=***  (token permanente, no vence)
META_WA_APP_SECRET=***
META_WA_VERIFY_TOKEN=viveroonline2026bot

# ePayco (sandbox)
EPAYCO_P_CUST_ID_CLIENTE=***
EPAYCO_P_KEY=***
EPAYCO_PUBLIC_KEY=***
EPAYCO_SECRET_KEY=***

# IA
GEMINI_API_KEY=***
```

---

## 📁 Estructura del proyecto

```
app/
├── auth/
│   ├── deps.py           — UserContext, guards de rol (DB > JWT siempre)
│   └── flow.py           — send_otp(email), verify_otp(email, code)
├── routes/
│   ├── auth.py           — endpoints OTP
│   ├── marketplace.py    — marketplace + cotizaciones (MARKUP 18%)
│   ├── public.py         — vitrina pública sin auth
│   ├── pedidos.py        — aprobación, checkout (markup × 1.18)
│   ├── kpis.py           — endpoints KPIs admin
│   ├── admin_ops.py      — CRUD admin (comunidad, suscripciones, inventario)
│   ├── pages.py          — rutas HTML
│   └── whatsapp.py       — webhook Meta Cloud API
├── services/
│   ├── supabase.py       — admin() + decode_jwt()
│   ├── whatsapp_meta.py  — cliente Meta Graph API v25.0
│   └── epayco.py         — cliente ePayco checkout
├── templates/
│   ├── landing.html
│   ├── auth_registro.html      — selección de rol (nuevo flujo)
│   ├── auth_phone.html         — email personalizado por rol
│   ├── auth_otp.html
│   ├── auth_onboarding.html    — bienvenida + datos del perfil
│   ├── admin_dashboard.html    — panel admin 6 secciones
│   ├── viverista_dashboard.html — inventario + pedidos pendientes
│   ├── comprador_dashboard.html — proyectos con totales × 1.18
│   ├── marketplace.html         — lista con precio × 1.18
│   ├── marketplace_detalle.html — detalle + privacidad suscripción
│   ├── checkout.html            — página de pago con ePayco
│   ├── pedido_detalle.html      — historial de transacción pagada
│   └── pagos_resultado.html     — resultado del pago + polling
└── main.py               — CORS + rate limiting middleware
```

---

## 🛡️ Seguridad implementada

- **RLS Supabase:** 41 políticas con check `is_anonymous = false`
- **JWT:** el rol siempre se verifica en BD (nunca solo en JWT)
- **EXECUTE revocado:** funciones SECURITY DEFINER solo accesibles por service_role
- **Rate limiting:** `/api/auth/otp/*` → 5 req/min, pagos → 5 req/min
- **Teléfono/WhatsApp de viveros:** NUNCA expuestos en ningún endpoint

---

## ⏳ Pendientes

| Tarea | Estado |
|-------|--------|
| Número real WhatsApp (SIM colombiana) | Pendiente |
| Publicar app Meta (verificación del negocio) | Pendiente |
| Activar ePayco producción (4 credenciales) | Pendiente |
| Plan Pro Supabase ($25/mes) a partir de 50+ usuarios | Pendiente |
| Activar precio real del Plan Inteligencia con cobro automático | Pendiente |
| Dashboard KPIs del comprador (`/mi-cuenta/inteligencia`) | Pendiente |

---

## 👤 Usuarios de prueba

| Email | Rol | Notas |
|-------|-----|-------|
| `promesaobca@gmail.com` | admin | Elena — sin vivero |
| `viveroportales@gmail.com` | viverista | vivero_id=10, 100 Petunias |
| `rosseobca@hotmail.com` | comprador | Miguel González, cliente_id=18, Plan Inteligencia activo |

---

*Última actualización: junio 2026 · ViveroOnline.com.co · Cajicá, Cundinamarca, Colombia*
