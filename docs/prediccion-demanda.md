# Predicción de Demanda — Sprint Plan

## Contexto

ViveroOnline conecta viveros del altiplano cundiboyacense con compradores institucionales y privados. El segmento de **paisajistas** (subset del rol "comprador") es de alto valor: diseñan e instalan jardines, espacios verdes y proyectos de arborización urbana — para clientes privados (residencial, comercial) y públicos (municipios, parques).

El paisajista enfrenta un problema crítico: **¿qué proyectos verdes van a salir en mi zona en los próximos 6-18 meses?**

Predicción de Demanda lo resuelve con señales anticipatorias externas:

- **SECOP II**: contratos públicos de arborización, jardinería, ornamental, reforestación
- **Curadurías Urbanas**: licencias de construcción con requisitos de compensación arbórea
- **CAMACOL**: pipeline de metros cuadrados a construir por región

**Output**: cada paisajista (rol comprador) ve en su dashboard *"Proyectos verdes que vienen en tu zona"* — un feed priorizado con: especies que se van a demandar, cantidades estimadas, fechas de licitación, entidad contratante, link al proceso SECOP. Doble valor:

1. **Procurement planning**: pre-contactar viveros para asegurar stock con tiempo
2. **Lead intel**: identificar licitaciones SECOP a las que aplicar como proveedor

## Estado actual de la BD

**Tablas creadas en Sprint 1** (migration `prediccion_demanda_sprint_1` aplicada 14-may-2026):

- `secop_procesos` — snapshot crudo de procesos de SECOP II (con `raw_data JSONB` como fuente de verdad + columnas denormalizadas)
- `secop_clasificacion` — análisis Gemini de cada proceso (especies, cantidades, fechas, flag `es_relevante`)

Views internas pre-existentes (transacciones de la propia plataforma, NO útiles para predicción en este momento):

- `v_public_demanda_municipio` — agregado de transacciones por ciudad
- `v_public_top_especies` — top especies vendidas

Pendientes para futuros sprints: `secop_paa`, `curaduria_licencias`, `camacol_indicadores`, `prediccion_demanda` (output agregado).

## Fuentes de Datos

### Tier 1 — APIs públicas (queriables hoy, sin auth)

**SECOP II** en datos.gov.co usando Socrata Open Data API (SODA):

| Dataset | ID | Para qué |
|---|---|---|
| Procesos de Contratación | `p6dx-8zbt` | Licitaciones en curso — **target de Sprint 1** |
| Contratos Electrónicos | `jbjy-vk9h` | Contratos adjudicados (historial) |
| PAA - Encabezado | `b6m4-qgqv` | Plan Anual de Adquisiciones — señal forward-looking |
| BPIN por Proceso | `d9na-abhe` | Vincula contratos con proyectos de inversión |
| Proveedores Registrados | `qmzu-gj57` | Competencia activa |

**Filtros**:
- Keywords en campo `objeto`: arborización, plantas, ornamental, siembra, jardinería, reforestación, vivero, silvicultura, paisajismo
- Municipios prioritarios: Sabana de Bogotá (Cajicá, Chía, Cota, Tabio, Tenjo, Tocancipá, Sopó, Zipaquirá, Funza, Mosquera, Madrid, Bogotá D.C.)
- Entidades clave: **Jardín Botánico de Bogotá José Celestino Mutis** (responsable de arborización del Distrito por Decreto 531/2010)

**Ejemplo de query**:
GET https://www.datos.gov.co/resource/p6dx-8zbt.json
?$where=upper(objeto_del_contrato) like '%ARBORIZACION%'
&$limit=100
&$order=fecha_de_publicacion_del_proceso DESC
### Tier 2 — Scraping requerido

**Curadurías Urbanas**: cada municipio gestiona las suyas, no hay API unificada. Foco para Sprint 4:

- Bogotá: 5 curadurías
- Cajicá: Curaduría 1 y 2
- Sabana norte: Chía, Cota, Tabio, Tenjo, Tocancipá

Las licencias urbanísticas deben incluir compensación arbórea por área construida (Decreto 1077/2015 + decretos municipales).

### Tier 3 — PDFs y datos manuales

**CAMACOL** (Cámara Colombiana de la Construcción): "Coordenada Urbana" trimestral con m² lanzados al mercado por región. Formato PDF/Excel.

## Arquitectura
┌─────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────────────┐
│   INGESTA   │ → │ CLASIFIC. IA │ → │  AGREGACIÓN │ → │       UI         │
│             │   │              │   │             │   │                  │
│ Vercel Cron │   │ Gemini extrae│   │ Agrupar por │   │ Dashboard        │
│ SECOP API   │   │  • especies  │   │  municipio  │   │ comprador        │
│ Pull semanal│   │  • cantidad  │   │  × especie  │   │ (paisajista)     │
│             │   │  • altura    │   │  × mes      │   │ +                │
│             │   │  • fecha     │   │             │   │ Endpoint público │
│             │   │  • relevante │   │             │   │ /api/prediccion  │
└─────────────┘   └──────────────┘   └─────────────┘   └──────────────────┘
## Schema BD — Estado actual

**Tablas creadas en Sprint 1** (DDL en `migrations`):

```sql
secop_procesos (
  proceso_id TEXT PK,
  entidad TEXT, entidad_nit TEXT,
  objeto TEXT,                    -- descripción del proceso
  cuantia_proceso NUMERIC,
  departamento TEXT, ciudad TEXT,
  fecha_publicacion DATE,
  fecha_recepcion_propuestas DATE,
  estado_proceso TEXT,
  modalidad_contratacion TEXT,
  url_proceso TEXT,
  raw_data JSONB NOT NULL,        -- payload completo (source of truth)
  fetched_at TIMESTAMPTZ,
  procesado_at TIMESTAMPTZ        -- NULL hasta que IA clasifica
)
+ índices: ciudad, fecha_publicacion, partial(procesado_at IS NULL)
+ RLS ON sin policies (solo service_role accede)

secop_clasificacion (
  proceso_id TEXT PK FK→secop_procesos ON DELETE CASCADE,
  especies_mencionadas TEXT[],
  cantidad_estimada INTEGER,
  altura_estimada_cm INTEGER,
  tipo_proyecto TEXT,             -- 'arborizacion_urbana' | 'reforestacion' | 'jardineria' | 'ornamental' | 'otro'
  fecha_estimada_entrega DATE,
  es_relevante BOOLEAN NOT NULL,  -- false = match keyword pero NO sobre plantas
  confianza NUMERIC,              -- 0..1
  modelo_usado TEXT,
  raw_response JSONB,             -- output completo del LLM (para debug)
  clasificado_at TIMESTAMPTZ
)
+ índice parcial: es_relevante=TRUE
+ RLS ON sin policies
```

**Tablas pendientes** (Sprints futuros): `secop_paa`, `curaduria_licencias`, `camacol_indicadores`, `prediccion_demanda` (output agregado).

## Sprint Breakdown

### Sprint 1 — Ingesta SECOP + Clasificación IA (en curso)

- ✅ DDL: tablas `secop_procesos`, `secop_clasificacion`
- ⏳ `app/services/secop.py` — cliente HTTP para Socrata API
- ⏳ `app/agents/secop_classifier.py` — Gemini extrae especies + cantidades + fechas + relevancia
- ⏳ `app/routes/ingesta.py` — endpoint `POST /api/ingesta/secop` (admin-only, trigger manual)
- ⏳ Registrar router en `app/main.py`
- ⏳ Smoke test: ingestar últimos 90 días, ver qué sale para Sabana de Bogotá

### Sprint 2 — Agregación + UI básica (próxima sesión)

- DDL: tabla `prediccion_demanda` (output agregado)
- Función de agregación: `secop_clasificacion` (relevante=TRUE) + `secop_procesos` → `prediccion_demanda`
- Endpoint: `GET /api/prediccion-demanda?municipio=X&meses=12`
- UI: nueva sección en `comprador_dashboard.html` con feed de proyectos verdes en su zona

### Sprint 3 — PAA + Cron automatizado

- DDL: tabla `secop_paa`
- Servicio: ingester de PAA
- Vercel Cron semanal: refrescar SECOP procesos + PAA + re-clasificar nuevos

### Sprint 4 — Curadurías

- Scraping curaduría Cajicá + curadurías Bogotá
- Tabla `curaduria_licencias`
- Conversión m² → árboles necesarios usando ratios del Decreto 531/2010

### Sprint 5 — CAMACOL

- Descarga + parsing de PDFs CAMACOL ("Coordenada Urbana")
- Tabla `camacol_indicadores`
- Modelo de proyección: m² CAMACOL → demanda esperada por especie

**Total**: 7-11 sesiones para sistema completo. MVP con solo SECOP (Sprints 1+2): 2-4 sesiones.

## Decisiones tomadas (al 14-may-2026)

- **Audiencia**: paisajista (subset del rol "comprador"). Sin rol nuevo en la app.
- **Surface UI**: `/comprador` dashboard, no `/viverista`.
- **Caso de uso primario**: procurement planning + lead intel (no production planning).
- **Schema**: `raw_data JSONB` como source of truth; campos extraídos son denormalización para queries rápidas.
- **Clasificador**: Gemini 2.5 Flash con structured output + flag `es_relevante` para filtrar falsos positivos.

## Open Questions

| # | Pregunta | Estado |
|---|---|---|
| 1 | ¿Hay spec del Auditor? | OPEN — sin spec, procedemos con nuestra arquitectura |
| 2 | ¿Target del producto? | ✅ Paisajista (rol comprador), dashboard `/comprador` |
| 3 | ¿Primer cliente real? | OPEN — la plataforma tiene 0 compradores. Para Sprint 1 el cliente es Elena en modo admin (smoke test). Antes de Sprint 2 conviene reclutar 1-2 paisajistas informales. |
| 4 | ¿Modelado en MVP? | OPEN — Sprint 2 decisión. Por defecto: sumar cantidades estimadas por (municipio, especie, mes). |
| 5 | ¿Cuántos meses adelante? | OPEN — propuesta: 12 meses por default (la mitad del ciclo de producción típico). |
| 6 | ¿Confidence intervals? | OPEN — para MVP: 'alta / media / baja' basado en `confianza` promedio. Intervalos numéricos en Sprint 5. |
| 7 | ¿Granularidad de especies? | OPEN — capa de normalización IA pendiente. Sprint 2: match por similitud de strings con tabla `plantas`. |
| 8 | ¿Acceso a Curadurías? | OPEN — research antes de Sprint 4 |
| 9 | ¿Suscripción CAMACOL? | OPEN — research antes de Sprint 5 |

## Referencias

- **SECOP II API**: https://www.datos.gov.co/Estad-sticas-Nacionales/SECOP-II-Procesos-de-Contrataci-n/p6dx-8zbt
- **Socrata Query Language (SoQL)**: https://dev.socrata.com/docs/queries/
- **Datos Abiertos Colombia**: https://www.datos.gov.co
- **Decreto 531/2010 Bogotá** — silvicultura urbana
- **Decreto 1077/2015 (Nacional)** — Decreto Único Reglamentario Sector Vivienda
- **CAMACOL Coordenada Urbana**: https://camacol.co
