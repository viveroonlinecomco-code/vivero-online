# ViveroOnline — Checklist de Lanzamiento
## Del Mac a producción en 45 minutos

---

## PASO 1 — Supabase (15 min)

### 1.1 Crear proyecto
- Ve a https://supabase.com → New Project
- Nombre: `vivero-online`
- Región: **South America (São Paulo)** — más cercana a Colombia
- Guarda el password

### 1.2 Ejecutar schemas (en orden)
En Supabase → SQL Editor → New Query:

**Primero:** pega y ejecuta todo el contenido de `schema_supabase.sql`
**Luego:** pega y ejecuta todo el contenido de `schema_mvp_additions.sql`

### 1.3 Crear bucket de imágenes
- Supabase → Storage → New Bucket
- Name: `plant-images`
- Public bucket: ✅ SÍ (activar)
- Clic en "Save"

### 1.4 Copiar tus credenciales
- Supabase → Settings → API
- Copia `Project URL` → es tu `SUPABASE_URL`
- Copia `service_role` (NO la anon key) → es tu `SUPABASE_SERVICE_KEY`

---

## PASO 2 — GitHub (5 min)

```bash
# En tu Mac, desde vivero-ai-marketplace/
git init  # si no tienes repo aún
git add app.py agent.py db.py graph.py requirements.txt
git add schema_supabase.sql schema_mvp_additions.sql
git add .gitignore
# ⚠️ VERIFICAR antes del commit:
git status  # el .env y secrets.toml NO deben aparecer
git commit -m "feat: ViveroOnline MVP listo para producción"
git remote add origin https://github.com/TU_USUARIO/vivero-online.git
git push -u origin main
```

**Estructura mínima requerida en el repo:**
```
vivero-ai-marketplace/
├── app.py
├── agent.py
├── db.py
├── graph.py          ← el que generamos en la sesión anterior
├── requirements.txt  ← el nuevo unificado
├── .gitignore
└── (NO debe haber .env ni secrets.toml)
```

---

## PASO 3 — Streamlit Cloud (10 min)

1. Ve a https://share.streamlit.io
2. Clic en **"New app"**
3. Conecta tu repositorio de GitHub
4. Configura:
   - **Repository:** `tu-usuario/vivero-online`
   - **Branch:** `main`
   - **Main file path:** `app.py`

5. Clic en **"Advanced settings"** → **Secrets**
   Pega esto (con tus valores reales):
   ```toml
   [supabase]
   url = "https://XXXXXXXXXX.supabase.co"
   service_key = "eyJhbGciOiJIUzI..."

   [gemini]
   api_key = "AIzaSy..."
   ```

6. Clic en **"Deploy!"**
7. Espera ~2 minutos → ¡Tu app está en línea!

Tu URL será algo como:
`https://vivero-online-XXXXX.streamlit.app`

---

## PASO 4 — Primera transacción real (5 min)

1. Abre tu app en el navegador
2. Regístrate como viverista en Cajicá
3. Ve a **Mi Catálogo → Agregar planta**
4. Sube la foto de la Sansevieria (test.jpg)
5. Confirma el análisis de IA → se guarda en Supabase
6. Pide a un colega que se registre y haga un pedido

Si todo funciona: tienes tu primera transacción B2B real. 🎉

---

## Verificación de producción

Después del deploy, verifica en Supabase → Table Editor:
- [ ] `viveristas` — tiene tus registros de prueba
- [ ] `catalogo_plantas` — tiene la Sansevieria con imagen_url
- [ ] `transacciones_b2b` — tiene la primera transacción
- [ ] `eventos_agente` — tiene eventos de tipo 'login', 'vision_scan'

---

## ¿Algo falla? Checklist de debug

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| "Error de conexión Supabase" | service_key incorrecta | Copia exactamente desde Settings → API → service_role |
| Imagen no aparece en catálogo | Bucket no es público | Storage → plant-images → Make public |
| "Error Gemini" | API key inválida | Verifica en aistudio.google.com → API keys |
| App no arranca | requirements.txt con conflictos | Usa exactamente el requirements.txt generado |
| Grafo LangGraph falla | Python 3.14 incompatible | En Streamlit Cloud usa Python 3.11 (se configura en Settings) |

### Forzar Python 3.11 en Streamlit Cloud:
Crea el archivo `.python-version` en la raíz del repo:
```
3.11
```

---

## Próximos hitos después de las primeras 10 transacciones

1. **WhatsApp** — notificar al vendedor cuando llegue un pedido (Twilio)
2. **Pagos** — integrar PSE o Wompi para cobro en línea
3. **Dashboard inversor** — desplegar `dashboard_inversor.py` como segunda página
4. **Dominio personalizado** — vivero-online.co en Streamlit Cloud (Settings → Custom domain)
