# 🌿 ViveroOnline.com.co

**Marketplace B2B AgTech** que conecta viveros ornamentales de la Sabana de Bogotá con compradores institucionales.

🌐 **Producción:** [app.viveroonline.com.co](https://app.viveroonline.com.co)

---

## ¿Qué es?

ViveroOnline es una plataforma que digitaliza el proceso de compra de plantas ornamentales a escala institucional. Los paisajistas, constructoras y conjuntos residenciales pueden cotizar, aprobar y pagar pedidos de múltiples viveros en una sola transacción.

---

## Stack

- **Backend:** FastAPI + Python 3.12
- **Frontend:** HTML + Tailwind CSS
- **Deploy:** Vercel (Serverless)
- **Base de datos:** Supabase PostgreSQL
- **Pagos:** ePayco
- **WhatsApp:** Meta Cloud API
- **IA:** Google Gemini Flash + LangGraph

---

## Flujo principal

```
1. Comprador busca plantas en el marketplace
2. Agrega plantas al carrito (de 1 o más viveros)
3. Envía cotización → cada viverista recibe WhatsApp
4. Viverista responde APROBAR o RECHAZAR
5. Cuando todos aprueban → comprador recibe WhatsApp con link de pago
6. Comprador paga (plantas + flete calculado por zona)
7. Viverista despacha → escribe ENVIADO
8. Comprador confirma con RECIBIDO → viverista recibe pago en 48h
```

---

## Estructura del repositorio

```
vivero-online/
├── api/
│   └── index.py          # Entry point Vercel
├── app/
│   ├── main.py           # FastAPI app + routers
│   ├── agents/           # LangGraph + Copilot + Gemini
│   ├── auth/             # JWT + OTP
│   ├── routes/           # Endpoints API
│   │   ├── marketplace.py
│   │   ├── pedidos.py    # Cotizaciones + flete + cron
│   │   ├── whatsapp.py   # Bot WhatsApp
│   │   ├── pagos.py      # ePayco webhook
│   │   └── ...
│   ├── services/
│   │   ├── supabase.py
│   │   ├── whatsapp_meta.py
│   │   ├── epayco.py
│   │   └── gemini.py
│   └── static/
│       └── js/vivero.js
├── templates/            # HTML páginas
│   ├── comprador_dashboard.html
│   ├── viverista_dashboard.html
│   ├── checkout.html
│   └── marketplace.html
├── vercel.json
└── requirements.txt
```

---

## Variables de entorno (Vercel)

```env
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
META_WA_TOKEN=
META_WA_PHONE_ID=
META_WA_VERIFY_TOKEN=
EPAYCO_PUBLIC_KEY=
EPAYCO_PRIVATE_KEY=
EPAYCO_P_CUST_ID=
EPAYCO_P_KEY=
GEMINI_API_KEY=
APP_BASE_URL=https://app.viveroonline.com.co
ENV=production
```

---

## Bot WhatsApp

**Número:** +57 311 5557154

| Rol | Comandos |
|-----|----------|
| Viverista | 📷 Foto, `precio`, `stock`, `agotado`, `disponible`, `APROBAR`, `RECHAZAR`, `ENVIADO` |
| Comprador | `RECIBIDO` |

---

## Modelo de precios

- **Markup plataforma:** 18% (oculto al comprador)
- **Flete:** calculado por zona geográfica y tier logístico (S/M/L/XL)
- **Recargo multi-vivero:** $40K–$120K por parada adicional

---

## Automatización

- Cotizaciones **aceptadas** vencen en **48 horas** automáticamente
- Cotizaciones **en pago** vencen en **2 horas**
- Cron job en Supabase ejecuta limpieza cada hora

---

## Estado actual (junio 2026)

- ✅ Marketplace funcional con búsqueda geoespacial
- ✅ Bot WhatsApp con identificación de plantas por IA
- ✅ Flujo de cotización completo con notificaciones
- ✅ Pagos reales con ePayco
- ✅ Logística coordinada con estados de entrega
- ✅ Multi-vivero con búsqueda automática de alternativas
- ✅ Vencimiento automático de cotizaciones
- ⏳ Google Gemini billing (pendiente aprobación)
- ⏳ Primer vivero externo activo

---

## Desarrollado por

**Mi JARDINERO** — Product Owner  
Sabana de Bogotá, Colombia 🇨🇴
