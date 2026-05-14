# Predicción de Demanda — Sprint Plan

## Contexto

ViveroOnline opera en B2B AgTech: conecta viveros del altiplano cundiboyacense con compradores institucionales y privados. Los viveros producen plantas en ciclos de 6-18 meses (desde semilla/esqueje a venta listo), lo que crea un problema crítico de planificación: **¿qué especies producir, en qué cantidad, para qué meses?**

Predicción de Demanda resuelve ese problema usando señales externas anticipatorias:

- **SECOP II**: contratos públicos de arborización, jardinería, ornamental, reforestación
- **Curadurías Urbanas**: licencias de construcción con requisitos de compensación arbórea
- **CAMACOL**: pipeline de metros cuadrados a construir por región

**Output**: cada viverista ve en su dashboard *"Demanda proyectada en tu zona para los próximos 6-12 meses"* — lista priorizada de especies con cantidades estimadas, fuentes y nivel de confianza.

## Estado actual de la BD

Existen 2 views relacionadas a demanda, ambas **internas** (basadas en transacciones de la propia plataforma):

- `v_public_demanda_municipio` — agregado de transacciones por ciudad (últimos 90 días)
- `v_public_top_especies` — top especies vendidas (últimos 90 días)

Con el inventario actual (2 plantas, 0 transacciones cerradas), estas views no son útiles para predicción. Sirven para reporting interno una vez haya volumen.

**No existen tablas para datos externos** — todo el módulo de predicción se diseña desde cero.

## Fuentes de Datos

### Tier 1 — APIs públicas (queriables hoy, sin auth)

**SECOP II** está en datos.gov.co usando Socrata Open Data API. Endpoints relevantes:

| Dataset | ID | Para qué |
|---|---|---|
| Procesos de Contratación | `p6dx-8zbt` | Licitaciones en curso |
| Contratos Electrónicos | `jbjy-vk9h` | Contratos adjudicados (historial) |
| PAA - Encabezado | `b6m4-qgqv` | Plan Anual de Adquisiciones — **señal forward-looking** |
| BPIN por Proceso | `d9na-abhe` | Vincula contratos con proyectos de inversión |
| Proveedores Registrados | `qmzu-gj57` | Competencia activa |

**Filtros relevantes**:
- Keywords en campo `objeto`: "plantas", "árboles", "arborización", "ornamental", "siembra", "jardinería", "reforestación", "vivero"
- Códigos UNSPSC: 10171500 (Plants and flowers), 10171502 (Live plant material), 70140000 (Forestry services)
- Entidades clave: **Jardín Botánico de Bogotá José Celestino Mutis** (responsable de arborización del Distrito por Decreto 531/2010)

**Ejemplo de query**:

GET https://www.datos.gov.co/resource/p6dx-8zbt.json
?$where=upper(objeto) like '%ARBORIZACION%'
&$limit=100

### Tier 2 — Scraping requerido

**Curadurías Urbanas**: cada municipio gestiona las suyas, no hay API unificada nacional. Para empezar, foco en:

- Bogotá: 5 curadurías
- Cajicá: Curaduría 1 y 2
- Sabana norte: Chía, Cota, Tabio, Tenjo, Tocancipá (sistemas individuales)

Las licencias urbanísticas deben incluir compensación arbórea por área construida (Decreto 1077/2015 + decretos municipales). Scraping necesario para extraer: dirección, m² construidos, requisito de árboles a plantar, fecha estimada de obra.

### Tier 3 — PDFs y datos manuales

**CAMACOL** (Cámara Colombiana de la Construcción): publica trimestralmente "Coordenada Urbana" con m² lanzados al mercado por región. Formato PDF + Excel. Procesamiento vía pdfplumber o descarga manual.

## Arquitectura propuesta

┌─────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────┐
│   INGESTA   │ → │ CLASIFIC. IA │ → │  AGREGACIÓN │ → │    UI    │
│             │   │              │   │             │   │          │
│ Vercel Cron │   │ Gemini extrae│   │ Agrupar por │   │ Dashboard│
│ SECOP API   │   │  • especies  │   │  municipio  │   │ viverista│
│ Pull semanal│   │  • cantidad  │   │  × especie  │   │ +        │
│             │   │  • altura    │   │  × mes      │   │ Endpoint │
│             │   │  • fecha     │   │             │   │  público │
└─────────────┘   └──────────────┘   └─────────────┘   └──────────┘

## Schema BD propuesto

```sql
-- ─── 1. Ingesta cruda ───
CREATE TABLE secop_procesos (
  proceso_id TEXT PRIMARY KEY,
  entidad TEXT,
  entidad_nit TEXT,
  tipo_proceso TEXT,
  objeto TEXT,
  cuantia_proceso NUMERIC,
  departamento TEXT,
  ciudad TEXT,
  fecha_publicacion DATE,
  fecha_recepcion_propuestas DATE,
  estado_proceso TEXT,
  raw_data JSONB,
  fetched_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_secop_procesos_ciudad ON secop_procesos(ciudad);
CREATE INDEX idx_secop_procesos_fecha ON secop_procesos(fecha_publicacion DESC);

CREATE TABLE secop_paa (
  paa_id TEXT PRIMARY KEY,
  entidad TEXT,
  año INTEGER,
  descripcion TEXT,
  cuantia NUMERIC,
  mes_estimado_inicio INTEGER,
  ciudad TEXT,
  raw_data JSONB,
  fetched_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE curaduria_licencias (
  licencia_id TEXT PRIMARY KEY,
  curaduria TEXT,            -- 'bogota_1', 'cajica_1', etc.
  municipio TEXT,
  fecha_licencia DATE,
  m2_construidos NUMERIC,
  arboles_compensacion INTEGER,
  direccion TEXT,
  raw_data JSONB,
  fetched_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE camacol_indicadores (
  indicador_id BIGSERIAL PRIMARY KEY,
  region TEXT,
  año INTEGER,
  trimestre INTEGER,
  m2_lanzados NUMERIC,
  m2_proyectados NUMERIC,
  fuente_documento TEXT,
  fetched_at TIMESTAMPTZ DEFAULT now()
);

-- ─── 2. Clasificación IA ───
CREATE TABLE secop_clasificacion (
  proceso_id TEXT PRIMARY KEY REFERENCES secop_procesos(proceso_id) ON DELETE CASCADE,
  especies_mencionadas TEXT[],
  cantidad_estimada INTEGER,
  altura_estimada_cm INTEGER,
  tipo_proyecto TEXT,           -- 'arborizacion_urbana', 'reforestacion', 'jardineria', 'ornamental'
  fecha_estimada_entrega DATE,
  confianza NUMERIC,            -- 0..1
  modelo_usado TEXT,            -- 'gemini-2.5-flash'
  clasificado_at TIMESTAMPTZ DEFAULT now()
);

-- ─── 3. Output agregado ───
CREATE TABLE prediccion_demanda (
  prediccion_id BIGSERIAL PRIMARY KEY,
  municipio TEXT NOT NULL,
  departamento TEXT,
  nombre_especie TEXT NOT NULL,
  planta_id INTEGER REFERENCES plantas(planta_id),
  mes_proyectado DATE NOT NULL,        -- truncado a YYYY-MM-01
  unidades_proyectadas INTEGER,
  rango_min INTEGER,                   -- para uncertainty intervals
  rango_max INTEGER,
  fuente TEXT NOT NULL,                -- 'secop_proceso', 'secop_paa', 'curaduria', 'camacol', 'agregado'
  confianza NUMERIC,
  fuentes_ids JSONB,                   -- array de IDs de las fuentes
  generado_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_pred_demanda_lookup
  ON prediccion_demanda(municipio, mes_proyectado);
```

## Sprint Breakdown

### Sprint 1 — Ingesta SECOP + Clasificación IA (1-2 sesiones)

- DDL: tablas `secop_procesos`, `secop_clasificacion`
- Endpoint backend: `POST /api/ingesta/secop` (admin-only, manual trigger inicial)
- Servicio: `app/services/secop_ingester.py` — query Socrata API, dedup, insert
- Agente: `app/agents/secop_classifier.py` — Gemini extrae especies + cantidades + fechas del campo `objeto`
- Validación: ingestar últimos 90 días, ver cuántos procesos relevantes salen para Sabana de Bogotá

### Sprint 2 — Agregación + UI básica (1-2 sesiones)

- DDL: tabla `prediccion_demanda`
- Script de agregación (puede vivir en backend Python o como SQL function)
- Endpoint: `GET /api/prediccion-demanda?municipio=X&meses=12`
- UI: nueva sección en `viverista_dashboard.html` reemplazando el placeholder actual de "Predicción IA por temporada"

### Sprint 3 — PAA + Cron automatizado (1-2 sesiones)

- DDL: tabla `secop_paa`
- Servicio: ingester de PAA
- Vercel Cron semanal: refrescar SECOP + PAA
- Pipeline de re-clasificación cuando llegan procesos nuevos

### Sprint 4 — Curadurías (2-3 sesiones)

- Scraping curaduría Cajicá (primero) + curadurías Bogotá
- Tabla `curaduria_licencias`
- Conversión m² → árboles necesarios usando ratios del Decreto 531/2010
- Insert en `prediccion_demanda` con fuente `'curaduria'`

### Sprint 5 — CAMACOL (1-2 sesiones)

- Descarga + parsing de PDFs CAMACOL ("Coordenada Urbana")
- Tabla `camacol_indicadores`
- Modelo de proyección: m² CAMACOL → demanda esperada por especie
- Calibración con datos de SECOP

**Total estimado**: 7-11 sesiones para sistema completo. **MVP con solo SECOP (Sprints 1+2): 2-4 sesiones**.

## Open Questions

Para responder antes de Sprint 1:

1. **¿Hay spec del Auditor?** Si sí, integrarlo con esta propuesta y resolver conflictos.
2. **¿Target principal?** Dashboard viverista funcional vs demo para pitch a inversores. Cambia priorización.
3. **¿Quién es el primer cliente?** Si Elena (vivero único actual) puede usar el módulo desde semana 1, calibramos con feedback real. Si no, hay que esperar a tener N viveristas.
4. **¿Modelado en MVP?** Para Sprint 2, ¿modelo simple (sumar contratos mes a mes) o algo más sofisticado (Prophet, tendencia + estacionalidad)?
5. **¿Cuántos meses hacia adelante?** 6, 12, 18, 24? Más meses = más incertidumbre = más complejidad.
6. **¿Confidence intervals?** Para nivel inversor sí; para viverista probablemente "alta / media / baja" alcanza.
7. **¿Granularidad de especies?** Match con tabla `plantas` por `nombre_cientifico` — pero SECOP usa nombres comunes inconsistentes ("urapan", "guayacán" hay 3+ especies con el mismo nombre común). Necesita capa de normalización IA.

Para responder antes de Sprint 4:

8. **¿Cómo se accede a Curaduría Cajicá?** ¿Portal web público? ¿Solicitud formal? ¿Datos abiertos del municipio?

Para responder antes de Sprint 5:

9. **¿Suscripción a CAMACOL Coordenada Urbana?** ¿Gratuito o pago?

## Referencias

- **SECOP II API docs**: https://dev.socrata.com/foundry/www.datos.gov.co/jbjy-vk9h
- **Datos Abiertos Colombia**: https://www.datos.gov.co
- **Decreto 531/2010 Bogotá** — silvicultura urbana: regula intervención arbórea, define responsabilidades del Jardín Botánico
- **Decreto 1077/2015 (Nacional)** — Decreto Único Reglamentario del Sector Vivienda
- **CAMACOL Coordenada Urbana**: https://camacol.co

## Decisiones de arranque

Pendientes hasta resolver Open Questions 1-3:
- ¿Empezar por Sprint 1 (Ingesta + IA) o por Sprint 2 (UI con datos mock para demo de inversores)?
- ¿Schema completo desde Sprint 1 (5 tablas), o iterativo (1 tabla a la vez)?
- ¿Cron desde Sprint 1, o trigger manual hasta Sprint 3?
