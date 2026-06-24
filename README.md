# 🌿 ViveroOnline.com.co

**Marketplace B2B AgTech** que conecta viveros ornamentales de la Sabana de Bogotá con compradores institucionales (paisajistas, constructoras, conjuntos residenciales).

🌐 **App:** [app.viveroonline.com.co](https://app.viveroonline.com.co)
📚 **Blog:** [viveroonline.com.co](https://www.viveroonline.com.co)

---

## ¿Qué es?

ViveroOnline es una plataforma que digitaliza el proceso de compra de plantas ornamentales a escala institucional. Los compradores B2B pueden cotizar, aprobar y pagar pedidos de **múltiples viveros en una sola transacción**, con entrega coordinada en toda la Sabana de Bogotá.

ViveroOnline opera como **mandatario comercial** de los viveros aliados: emite facturación electrónica al comprador (DIAN Res. 165/2023), gestiona pagos, logística y garantías a cambio de una comisión de orquestación.

---

## Stack

- **Backend:** FastAPI + Python 3.12
- **Frontend:** HTML + Tailwind CSS + Jinja2
- **Deploy:** Vercel (Serverless Functions)
- **Base de datos:** Supabase PostgreSQL + Edge Functions
- **Pagos:** ePayco (Colombia)
- **WhatsApp:** Meta Cloud API (bot transaccional)
- **Email:** Resend SMTP (auth + notificaciones)
- **IA conversacional:** Google Gemini Flash (agentes especializados)
- **Visión:** Gemini Vision (identificación de plantas por foto)
- **Lead Generation:** SECOP II API (datos.gov.co — Socrata)
- **Blog/CMS:** WordPress (viveroonline.com.co)
- **Auth:** Supabase Auth (OTP por email)

---

## Flujo principal

```
1. Comprador busca plantas en el marketplace (catálogo multi-vivero)
2. Agrega plantas al carrito (de 1 o más viveros)
3. Envía cotización → cada viverista recibe WhatsApp
4. Viverista responde APROBAR o RECHAZAR
5. Cuando todos aprueban → comprador recibe WhatsApp con link de pago
6. Comprador paga (plantas + flete calculado por zona)
7. Viverista despacha → escribe ENVIADO
8. Comprador confirma con RECIBIDO → viverista recibe pago vía mandato (3 días hábiles)
```

Cotizaciones con vivero sin stock activan **búsqueda automática de vivero alternativo** con confirmación del comprador.

---

## Estructura del repositorio

```
vivero-online/
├── api/
│   └── index.py                 # Entry point Vercel
├── app/
│   ├── main.py                  # FastAPI app + routers
│   ├── agents/                  # Agentes IA orquestados
│   │   ├── base.py              # Clase Agent + filtros confidencialidad
│   │   ├── router.py            # Clasificador + bloqueo de extracción
│   │   ├── ai_ceo.py            # Híbrido FAQ + Gemini
│   │   ├── landscape_advisor.py # Recomendador anclado a inventario real
│   │   ├── plant_identifier.py  # Identificación por foto (Gemini Vision)
│   │   ├── inventory_builder.py # Viverista gestiona stock por WhatsApp
│   │   └── ...                  # Otros: ver Arquitectura de Agentes
│   ├── auth/                    # Supabase Auth + OTP
│   ├── data/
│   │   └── faq_config.json      # FAQ del ai_ceo (editable sin tocar código)
│   ├── routes/                  # Endpoints API
│   │   ├── marketplace.py
│   │   ├── pedidos.py           # Cotizaciones + flete + crons
│   │   ├── whatsapp.py          # Bot WhatsApp
│   │   ├── pagos.py             # ePayco webhook
│   │   ├── secop.py             # Lead-radar (SECOP II)
│   │   └── ...
│   ├── services/
│   │   ├── supabase.py
│   │   ├── whatsapp_meta.py
│   │   ├── epayco.py
│   │   ├── gemini.py
│   │   ├── secop_client.py      # Cliente Socrata API
│   │   ├── faq_local.py         # Matcher de FAQ (UTF-8, umbral estricto)
│   │   ├── datos_fiscales_wa.py # Flujo de mandato + datos fiscales
│   │   └── ...
│   └── static/
│       └── js/vivero.js
├── templates/                   # HTML páginas
│   ├── comprador_dashboard.html
│   ├── viverista_dashboard.html
│   ├── checkout.html
│   ├── marketplace.html
│   └── admin_*.html
├── vercel.json
└── requirements.txt
```

---

## Variables de entorno (Vercel)

```env
# Supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=

# WhatsApp (Meta Cloud API)
META_WA_TOKEN=
META_WA_PHONE_ID=
META_WA_VERIFY_TOKEN=

# Pagos (ePayco)
EPAYCO_PUBLIC_KEY=
EPAYCO_PRIVATE_KEY=
EPAYCO_P_CUST_ID=
EPAYCO_P_KEY=

# IA (Gemini)
GEMINI_API_KEY=

# SECOP II (datos.gov.co)
SOCRATA_APP_TOKEN=

# App
APP_BASE_URL=https://app.viveroonline.com.co
ENV=production
```

SMTP (Resend) configurado dentro de Supabase Auth — no requiere variable en Vercel.

---

## Bot WhatsApp

**Número:** +57 311 555 7154

### Para viveristas

| Comando / acción | Función |
|---|---|
| 📷 Foto de planta | Identificación automática + creación de inventario |
| `precio`, `stock`, `agotado`, `disponible` | Gestión rápida de inventario |
| `APROBAR` / `RECHAZAR` | Responder cotizaciones (con validación de mandato firmado) |
| `ENVIADO` | Confirmar despacho |

### Para compradores

| Comando / acción | Función |
|---|---|
| Conversación natural | Asesoría de plantas (catálogo real, sin invenciones) |
| `RECIBIDO` | Confirmar recepción y liberar pago al vivero |

### Onboarding de viverista nuevo

Flujo guiado por WhatsApp para datos fiscales y aceptación del Contrato de Mandato Comercial — sin Gemini, cero ambigüedad.

---

## Modelo de precios

**Markup plataforma:** 20% sobre el precio base del viverista (oculto al comprador). Composición:

| Concepto | % |
|---|---|
| Comisión plataforma | 8% |
| Coordinación logística | 6% |
| Garantía de entrega | 4% |
| Margen operativo | 2% |

**Flete:** calculado por zona geográfica y tier logístico (S/M/L/XL).
**Recargo multi-vivero:** $40K–$120K COP por parada adicional.

---

## Documentación legal

- **Contrato de Mandato Comercial** publicado en [viveroonline.com.co/contrato-de-mandato-comercial](https://www.viveroonline.com.co/contrato-de-mandato-comercial/)
- Aceptación por WhatsApp con registro de fecha, número y versión
- Cumplimiento: DIAN Resolución 165/2023 (facturación electrónica), Ley 1581/2012 (datos personales), Ley 527/1999 (firma digital)

---

## Arquitectura de Agentes IA

El bot orquesta agentes especializados con clasificador inteligente y capas de confidencialidad.

### Agentes transaccionales (Gemini activo)

| Agente | Función |
|---|---|
| `landscape_advisor` | Recomendación de plantas anclada al inventario real (nunca inventa especies/precios) |
| `plant_identifier` | Identificación de plantas por foto (Gemini Vision) |
| `inventory_builder` | Gestión de inventario para viveristas |

### Agentes optimizados (sin Gemini o híbridos)

| Agente | Estado |
|---|---|
| `ai_ceo` | Híbrido: FAQ local primero (cero costo, <50ms), Gemini como fallback inteligente |
| `ai_architect` | Lobotomizado: redirige a blog SEO |
| `demand_predictor` | Deshabilitado hasta tener datos históricos reales del marketplace |
| `researcher` | Pivote a lead-radar interno (no conversacional) |
| `startup_builder` | En conversión a flujo estructurado de onboarding (4 hitos) |

### Capas de seguridad

- Filtros regex contra intentos de extracción (tecnología, datos de usuarios, prompts internos)
- Reglas de confidencialidad inyectadas en el system prompt de cada agente
- Bloqueo previo al modelo: el clasificador rechaza patrones de inyección antes de llamar a Gemini

---

## SECOP II — Pipeline de Lead Generation

Cliente HTTP contra la API pública de SECOP II (datos.gov.co) para detección de licitaciones públicas relacionadas con paisajismo, jardinería y reforestación en municipios de la Sabana de Bogotá.

- **Dataset target:** `p6dx-8zbt` (Procesos de Contratación)
- **Tablas:** `secop_procesos`, `secop_clasificacion`
- **Panel:** Oportunidades SECOP en dashboard admin (filtros por ciudad/especie/relevancia IA)
- **Modelo de negocio:** Plan Inteligencia (suscripción para paisajistas/constructoras)

---

## Automatización

- Cotizaciones **aceptadas** vencen en **48 horas** automáticamente
- Cotizaciones **en pago** vencen en **2 horas**
- Crons en Supabase: limpieza de cotizaciones vencidas, vencimiento horario, limpieza de usuarios huérfanos

---

## Estado actual (junio 2026)

- ✅ Marketplace funcional con búsqueda geoespacial
- ✅ Bot WhatsApp con identificación de plantas por IA
- ✅ Flujo de cotización completo con notificaciones
- ✅ Pagos reales con ePayco
- ✅ Logística coordinada con estados de entrega
- ✅ Multi-vivero con búsqueda automática de alternativas
- ✅ Vencimiento automático de cotizaciones
- ✅ Contrato de Mandato Comercial publicado y operativo
- ✅ SMTP Resend (entregabilidad a Hotmail/Outlook resuelta)
- ✅ Pipeline SECOP II (ingesta + clasificación)
- ✅ Arquitectura de agentes consolidada (reducción ~70% en costo Gemini)
- ✅ Blog WordPress activo (25+ entradas, alimenta SEO)
- ⏳ Plan Inteligencia (Premium SECOP II) — validación manual previa
- ⏳ Flujo de onboarding viverista por 4 hitos
- ⏳ Wompi Split Payments (pospuesto hasta +7 viveros activos)

---

## Desarrollado por

**Mi JARDINERO** — Founder & CEO
Sabana de Bogotá, Colombia 🇨🇴
