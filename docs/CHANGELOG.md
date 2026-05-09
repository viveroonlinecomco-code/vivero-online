# Changelog

## v0.2.0 — 2026-04-27

### ✨ Nuevas funcionalidades

#### 🔄 Webhook bidireccional WhatsApp (Twilio)
- `POST /api/whatsapp/webhook` recibe mensajes entrantes
- Validación de firma Twilio en producción
- Identificación automática del usuario por número WhatsApp
- Si el mensaje trae imagen → identifica planta con YOLO + Gemini
- Si es texto → enrutado al LangGraph (8 agentes)
- Persiste sesión en `sesiones_agente` con historial completo
- Comandos: `ayuda`, `salir`, `menú`
- Responde via TwiML (XML) compatible con Twilio

#### 🌿 YOLO-11 preprocessing
- `app/services/yolo.py` integrado con Ultralytics HUB API
- Detecta plantas en la imagen y recorta antes de enviar a Gemini
- Mejora precisión en fotos con fondos ruidosos
- **Fallback elegante**: si YOLO falla o no está configurado, pasa imagen original a Gemini sin romper el flujo
- Metadata (`yolo_meta`) expuesta en respuesta para debugging

#### 💳 ePayco (PSE + Tarjetas)
- `app/services/epayco.py` con Checkout API
- `POST /api/pagos/iniciar` crea pago + payload firmado para checkout
- `POST /api/pagos/confirmacion` webhook con validación SHA256
- `GET /api/pagos/estado/{pago_id}` para polling
- Página `/pagos/resultado` con estados: aprobado / pendiente / rechazado
- Comisión plataforma 5% (configurable)
- Integrada con tabla `pagos` existente + columna `epayco_id` nueva
- Migración 37: añade `epayco_id` con índice

#### 📊 Dashboard inversor público
- `GET /api/public/flywheel` — sin autenticación, datos anonimizados
- Página `/inversores` con KPIs en tiempo real
- 4 vistas SQL nuevas (migración 38) **anonimizadas**:
  - `v_public_flywheel` — KPIs globales con GMV redondeado
  - `v_public_crecimiento` — últimas 12 semanas
  - `v_public_demanda_municipio` — agregado por ciudad (sin nombres de viveros)
  - `v_public_top_especies` — top 20 plantas más populares

### 🔧 Cambios técnicos

- `app/main.py` registra 3 routers nuevos: `whatsapp`, `pagos`, `public`
- `app/config.py` añade variables `epayco_*`
- `app/templates/pagos_resultado.html` y `inversores.html` con estilo Stitch
- `requirements.txt` sin cambios (todas las deps ya estaban)
- `.env.example` actualizado con bloques ePayco + comentarios de webhooks

### 🗄️ Migraciones DB aplicadas

- `37_add_epayco_id_to_pagos`
- `38_public_investor_views_anonymized`

### 📝 Endpoints añadidos

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| POST | `/api/whatsapp/webhook` | (firma Twilio) | Mensaje WhatsApp entrante |
| GET  | `/api/whatsapp/webhook` | — | Health check |
| POST | `/api/pagos/iniciar` | user | Crea sesión ePayco |
| POST | `/api/pagos/confirmacion` | (firma ePayco) | Webhook resultado |
| GET  | `/api/pagos/estado/{id}` | user | Polling estado |
| GET  | ~~`/api/public/flywheel`~~ | — | KPIs anonimizados *(removido en v0.3)* |
| GET  | `/api/public/health` | — | Health |
| GET  | `/inversores` | — | Dashboard público |
| GET  | `/pagos/resultado` | — | Página post-pago |

### ⚙️ Variables nuevas en Vercel

```
EPAYCO_PUBLIC_KEY
EPAYCO_PRIVATE_KEY      (secret)
EPAYCO_P_CUST_ID        (secret)
EPAYCO_P_KEY            (secret)
```

### 🔮 Pendientes para v0.3

- [ ] Notificación al viverista por WhatsApp cuando recibe cotización/venta
- [ ] Envío de comprobante de pago aprobado
- [ ] Webhook ePayco enviado a partir de un cron de reintentos si la confirmación se demora
- [ ] Página `/inversores` con gráfica de mapa (mapbox o leaflet)
- [ ] Modelo YOLO custom entrenado en plantas ornamentales colombianas

---

## v0.3.0 — 2026-04-28

### 🔐 Dashboard Inversor con código de acceso

Migración del dashboard inversor de "totalmente público" a "requiere código de invitación".

#### Nuevos endpoints (`/api/inversores/*`)
- `POST /api/inversores/login` — valida código → emite cookie firmada (HMAC-SHA256, 24h TTL)
- `POST /api/inversores/logout` — borra cookie
- `GET  /api/inversores/me` — devuelve nombre del inversor activo
- `GET  /api/inversores/flywheel` — KPIs anonimizados (requiere cookie)
- `POST /api/inversores/codigos` — admin genera nuevo código de invitación
- `GET  /api/inversores/codigos` — admin lista todos los códigos emitidos

#### Páginas
- `/inversores/login` — formulario de ingreso con código (estilo Stitch)
- `/inversores` — ahora redirige a /login si no hay cookie válida; muestra nombre del inversor en header + botón logout

#### Seguridad
- Cookie firmada con `SUPABASE_JWT_SECRET` usando HMAC-SHA256 y comparación constant-time
- `httponly + secure + samesite=lax` en producción
- Tracking de `total_accesos`, `primer_uso_en`, `ultimo_uso_en`, `expira_en` en `invitaciones_inversor`
- `/api/public/flywheel` REMOVIDO (los datos ya no se exponen sin auth)

### 🗄️ Migración DB
- `39_invitaciones_inversor` (aplicada): tabla con RLS solo-admin
