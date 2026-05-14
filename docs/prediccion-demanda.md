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
