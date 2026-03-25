# 🌿 ViveroOnline — Marketplace AgTech B2B

Marketplace de plantas para viveristas de la Sabana de Bogotá.
Deploy en Vercel + Supabase + Gemini 2.5 Flash.

## Variables de entorno requeridas en Vercel

En Vercel → Settings → Environment Variables:

| Variable | Valor |
|----------|-------|
| `SUPABASE_URL` | `https://rjqnlmnjyfudklihmkym.supabase.co` |
| `SUPABASE_SERVICE_KEY` | tu service_role key |
| `GEMINI_API_KEY` | tu API key de Gemini |

## Estructura
```
api/
  index.py   ← FastAPI (todos los endpoints)
  db.py      ← conexión Supabase
  agent.py   ← Gemini Vision + Chat
  graph.py   ← LangGraph 8 agentes
static/
  index.html ← Frontend completo (HTML/JS)
vercel.json  ← configuración Vercel
requirements.txt
```
