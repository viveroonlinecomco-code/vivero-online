# 🌿 ViveroOnline — Marketplace AgTech B2B

Marketplace B2B para viveristas de la Sabana de Bogotá.
Conecta oferta y demanda de plantas ornamentales con IA integrada.

## Stack
- **Frontend:** Streamlit Cloud
- **Base de datos:** Supabase (PostgreSQL)
- **IA:** Gemini 2.5 Flash (identificación de plantas + 8 agentes LangGraph)
- **Almacenamiento:** Supabase Storage (imágenes)

## Funcionalidades MVP
- Registro de viveristas por municipio
- Catálogo con identificación de plantas por foto (IA)
- Marketplace B2B entre viveristas
- Transacciones con control de stock
- Chat con agente IA (8 nodos LangGraph)
- Data Flywheel para métricas de tracción

## Deploy rápido

Ver `LAUNCH_CHECKLIST.md` para el paso a paso completo.

```bash
# Local
cp .env.example .env   # completar con tus keys
pip install -r requirements.txt
streamlit run app.py
```

## Estructura
```
app.py          ← UI Streamlit (5 páginas)
db.py           ← Operaciones Supabase
agent.py        ← Conexión con Gemini + LangGraph
graph.py        ← Grafo multi-agente (8 nodos)
requirements.txt
schema_supabase.sql          ← Schema principal
schema_mvp_additions.sql     ← Adiciones para el MVP
```
