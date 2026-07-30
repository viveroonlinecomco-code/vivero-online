# 🌱 viveroonline.com.co

**B2B AgTech Marketplace** conectando viveros ornamentales con compradores institucionales (paisajistas, constructoras, conjuntos residenciales) — y ahora también con **compradores particulares B2C**.

Ubicado en la Sabana de Bogotá, Colombia. Desarrollado con FastAPI + Supabase + Vercel.

---

## 🎯 Estado del proyecto

| Canal | Estado | Fecha |
|-------|--------|-------|
| B2B (empresas registradas) | 🟢 En producción | Julio 2024 |
| B2C Guest (compra particular) | 🟡 Testing final | Julio 2026 |
| Split payment ePayco | 🔴 Pendiente | Agosto 2026 |
| Integración Quick Última Milla | 🔴 Pendiente | Agosto 2026 |

**Primera venta B2C real proyectada**: ~10 agosto 2026

---

## 🏗 Arquitectura

### Stack
- **Backend**: FastAPI (Python 3.11) sobre Vercel Serverless
- **Base de datos**: Supabase (PostgreSQL 17.6, región sa-east-1)
- **Auth**: Supabase Auth con OTP por WhatsApp
- **Frontend**: HTML + Tailwind CSS + Vanilla JavaScript
- **Pagos**: ePayco (checkout, webhooks, split payment próximo)
- **Logística**: Quick Última Milla (white-label, en integración)
- **IA**: OpenAI GPT + Gemini para identificación de plantas
- **Notificaciones**: WhatsApp Business API vía Meta Cloud

### Estructura del repo
```
app/
├── routes/              # Endpoints FastAPI
│   ├── auth.py         # Login, OTP, onboarding
│   ├── marketplace.py  # Catálogo B2B + guest público
│   ├── checkout_guest.py  # Flujo B2C completo
│   ├── pedidos.py      # Cotizaciones B2B
│   ├── pagos.py        # ePayco integration + webhook
│   ├── admin_ops.py    # Panel admin
│   └── pages.py        # Rutas HTML
├── services/
│   ├── precios.py      # Motor matricial de precios
│   ├── config_global.py  # Config runtime (matriz comercial)
│   ├── logistica.py    # Generación de entregas
│   ├── epayco.py       # SDK ePayco
│   └── supabase.py     # Cliente admin
├── templates/          # HTML templates
├── auth/               # Deps de autenticación
└── main.py            # Entrypoint FastAPI
```

---

## 💰 Modelo Comercial

### Regla del Productor v2 (inmutable)
El viverista SIEMPRE recibe el precio mayorista que publicó. ViveroOnline absorbe todos los descuentos comerciales y costos fintech. **El viverista NUNCA ve cuánto paga el comprador.**

### Motor matricial por categoría de producto

**Markups B2C** (aplicados al precio mayorista para obtener precio vitrina):

| Categoría | Markup |
|-----------|--------|
| plantas ornamentales | 20% |
| árboles | 20% |
| materas | 25% |
| sustrato / abonos | 17% |
| accesorios | 25% |
| otros (default) | 20% |

**Descuentos B2B** aplicables a compras ≥ 5 SMLMV ($8.754.525 en 2026):

| Categoría | Inmediato | 30d | 60d | 90d |
|-----------|-----------|-----|-----|-----|
| plantas / árboles | 12% | 9% | 6% | 3% |
| materas | 10% | 7% | 4% | 1% |
| sustrato | 9% | 6% | 3% | 0% |
| accesorios | 14% | 11% | 8% | 5% |

**Márgenes netos constantes**:
- plantas / árboles / sustrato: 8% neto
- accesorios: 11% neto
- materas: 15% neto (categoría más rentable)

**Costos fintech** (Kontempo, proyectados, inactivos): 30d=3%, 60d=6%, 90d=9%.

---

## 🌱 Reglas B2C Guest

- Marketplace filtrado solo tier logístico **S y M** (excluye L y XL)
- **Máximo 10 unidades** por producto
- **Máximo 120 plantas** por compra total
- Reserva stock 15 min al iniciar pago
- Compra directa sin aprobación viverista (viverista notificado inmediato)
- Datos mínimos legales: nombre, cédula/NIT, email, WhatsApp, dirección, ciudad
- Aviso legal producto perecedero obligatorio (Ley 1480 art. 47)
- Ventana reclamos: **2 horas** post-entrega (B2B: 12 horas)
- Reembolso 100% o parcial 20% con evidencia
- Guest recurrente detectado por email normalizado (LOWER + TRIM)
- Conversión Guest → B2B automática tras 2 compras (Fase 13)

---

## 🚦 Rutas principales

### Público (sin auth)
```
GET  /                              Landing
GET  /marketplace                   Catálogo (guest ve filtrado S+M)
GET  /marketplace/producto/{id}     Detalle producto
GET  /carrito-guest                 Carrito (localStorage)
GET  /checkout-guest                Formulario compra guest
GET  /pagos/resultado               Post-pago (B2B y guest)
GET  /habeas-data                   Política de privacidad
GET  /terminos                      Términos y condiciones
POST /api/public/marketplace        Endpoint catálogo (JSON)
POST /api/public/checkout-guest/validate-cart
POST /api/public/checkout-guest/calcular-flete
POST /api/public/checkout-guest/create-order
POST /api/pagos/confirmacion        Webhook ePayco
```

### B2B (con auth)
```
GET  /comprador                     Dashboard comprador
GET  /viverista                     Dashboard viverista
GET  /admin                         Dashboard admin
POST /api/marketplace/cotizacion    Crear/actualizar cotización
POST /api/pedidos/{cot_id}/aprobar  Aprobar cotización
POST /api/pagos/iniciar             Iniciar pago B2B
```

---

## 🔐 Variables de entorno

Requeridas en Vercel:

```bash
# Supabase
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=

# ePayco
EPAYCO_P_CUST_ID=
EPAYCO_P_KEY=
EPAYCO_PRIVATE_KEY=
EPAYCO_PUBLIC_KEY=

# WhatsApp Business API
WA_TOKEN=
WA_PHONE_NUMBER_ID=
WA_VERIFY_TOKEN=

# Admin
ADMIN_WHATSAPP_NOTIF=+573178543819

# Cron
CRON_SECRET=

# App
APP_BASE_URL=https://app.viveroonline.com.co
```

---

## 📦 Dependencias clave

Ver `requirements.txt` para la lista completa. Principales:

- `fastapi` — framework web
- `pydantic[email]` — validación de datos (incluye email-validator)
- `email-validator>=2.0.0` — requerido por Pydantic EmailStr
- `supabase-py` — cliente Supabase
- `httpx` — cliente HTTP async
- `python-jose` — JWT tokens
- `openai` — GPT integration
- `google-generativeai` — Gemini integration

---

## 🚀 Deploy

### Vercel (automático)
Cada push a `main` deploya automáticamente a producción (`app.viveroonline.com.co`).
Cada push a una rama `feat/*` deploya a preview URL.

### Convención de trabajo
Cada fase o fix va en rama separada con PR + merge. Nunca commits directos a `main`.

Ramas activas:
- `feat/fase-8-guest` — Marketplace guest B2C
- `feat/fase-10-checkout` — Guest checkout completo

---

## 🗺 Roadmap (agosto 2026)

### 🔴 P1 — Bloquean launch
- [ ] Fase 7 — Home renovada con 2 botones (COMPRAR YA / COTIZAR)
- [ ] Fase 11 — Split payment ePayco (75% viverista / 20% ViveroOnline / flete → Quick)
- [ ] Fase 9 — Integración Quick Última Milla (esperando credenciales)
- [ ] WhatsApp Meta template aprobado para notificaciones

### 🟡 P2 — Post-launch inmediato
- [ ] Fase 12 — Sistema reembolsos (2h B2C / 12h B2B)
- [ ] Fase 13 — Conversión Guest → B2B automática
- [ ] Fase 6 — Dashboard admin renovado (8 pestañas)
- [ ] Fase 14 — Cron alerta stock viveristas cada 15 días

### 🟢 P3 — Legal y operativo
- [ ] Templates email Supabase Auth en español
- [ ] Política de Tratamiento de Datos en WordPress
- [ ] Contrato Mandato v2.0 revisión abogado
- [ ] Firma mandatos con Chaparro, Edgar, La Victoria

---

## 👥 Viveristas activos

| ID | Nombre | Productos | Mandato |
|----|--------|-----------|---------|
| 11 | Viveroonline SAS (Elena Obando) | 103 | ✅ Firmado |
| 13 | Edgar Abonos los parches | 1 | ⏳ Pendiente |
| 14 | Chaparro materas | 29 | ⏳ Pendiente |
| 16 | La Victoria | 0 | ⏳ Pendiente |

---

## 📞 Contactos

- **Elena Obando** (Founder & CEO): +573178543819
- **WhatsApp bot institucional**: +573115557154
- **Email operativo**: viveroonline.com.co@gmail.com
- **Web**: https://viveroonline.com.co
- **App**: https://app.viveroonline.com.co

---

## 📜 Licencia

Proprietario — © 2026 viveroonline.com.co. Todos los derechos reservados.
