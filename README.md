# 🚀 Fase 4 — Instrucciones de deploy

**Rama**: `b2b-b2c-v2` (NUNCA main hasta validar)
**Fecha**: 21 julio 2026

## 📦 Archivos incluidos

| Path en el repo | Archivo entregado | Nuevo/Modificado |
|-----------------|-------------------|-------------------|
| `migrations/2026-07-21_categorias_matriz_comercial.sql` | `2026-07-21_categorias_matriz_comercial.sql` | 🆕 NUEVO |
| `app/services/config_global.py` | `config_global.py` | ✏️ Extendido |
| `app/services/precios.py` | `precios.py` | 🆕 NUEVO |
| `app/services/fintech_stub.py` | `fintech_stub.py` | 🆕 NUEVO |
| `app/routes/pedidos.py` | `pedidos.py` | ✏️ Refactor |
| `app/routes/pagos.py` | `pagos.py` | ✏️ Fix hardcoded 0.05 |
| `app/routes/fintech.py` | `fintech.py` | 🆕 NUEVO |

## ⚠️ Cambio adicional en `main.py`

Registrar el nuevo router de fintech. Agregar estas 2 líneas junto a las de admin_config:

**1. En la sección de imports (junto a `admin_config_router`)**:

```python
from app.routes.admin_ops import router as admin_ops_router
from app.routes.admin_config import router as admin_config_router
from app.routes.fintech import router as fintech_router   # ← NUEVA
from app.routes.pedidos import router as pedidos_router
```

**2. En la sección de includes (junto al de admin_config)**:

```python
app.include_router(admin_ops_router)
app.include_router(admin_config_router)
app.include_router(fintech_router)   # ← NUEVA
app.include_router(pedidos_router)
```

## 📋 Orden de ejecución sugerido

### 1️⃣ Correr la migration SQL en Supabase PRIMERO

Antes de subir el código, correr la migration en el SQL Editor de Supabase.

**Verificaciones post-migration** (descomentar al final del archivo SQL o correr aparte):

```sql
-- Verificar categorías asignadas en plantas
SELECT categoria_producto, COUNT(*) FROM plantas GROUP BY categoria_producto;

-- Verificar función helper
SELECT get_categoria_producto(32);

-- Verificar matriz cargada
SELECT valor::jsonb -> 'markup_b2c' -> 'materas' FROM configuracion_global WHERE clave='matriz_comercial';
-- → debería devolver 0.25
```

### 2️⃣ Subir el código a la rama b2b-b2c-v2

Copiar los archivos a sus paths correspondientes y hacer commit:

```bash
git checkout b2b-b2c-v2

# Copiar archivos:
cp <descargado>/config_global.py app/services/config_global.py
cp <descargado>/precios.py app/services/precios.py
cp <descargado>/fintech_stub.py app/services/fintech_stub.py
cp <descargado>/pedidos.py app/routes/pedidos.py
cp <descargado>/pagos.py app/routes/pagos.py
cp <descargado>/fintech.py app/routes/fintech.py
mkdir -p migrations
cp <descargado>/2026-07-21_categorias_matriz_comercial.sql migrations/

# Editar main.py agregando las 2 líneas del router fintech (ver arriba)

# Commit atómico
git add app/services/config_global.py \
        app/services/precios.py \
        app/services/fintech_stub.py \
        app/routes/pedidos.py \
        app/routes/pagos.py \
        app/routes/fintech.py \
        app/main.py \
        migrations/2026-07-21_categorias_matriz_comercial.sql

git commit -m "feat(fase-4): motor comercial matricial por categoría

- Migration: categoria_producto en plantas + inventario + auto-clasificación
- Nueva clave matriz_comercial (JSON) en configuracion_global
- config_global.py extendido con get_matriz_comercial, get_markup_categoria,
  get_descuento_b2b, get_costo_fintech, get_comision_bruta, get_comision_neta
- precios.py NUEVO: motor calcular_precios_pedido con desglose item-por-item
- pedidos.py: reemplazo MARKUP_PLATAFORMA=0.20 hardcoded, eliminado duplicado
  confirmar_vivero_alternativo
- pagos.py: reemplazo 0.05 hardcoded por desglose desde motor
- fintech_stub.py NUEVO: stub genérico (verificar_linea_credito, solicitar_credito,
  procesar_webhook_fintech)
- fintech.py NUEVO: endpoint webhook + admin verify + status
- main.py: registra fintech_router

Regla del Productor v2: viverista siempre recibe precio mayorista publicado.
Fintech inactiva (fintech_activa=false). Solo TC empresarial/ePayco activo."

git push origin b2b-b2c-v2
```

### 3️⃣ Verificar en deploy preview de Vercel

Después de push, Vercel deploya el preview de `b2b-b2c-v2` en ~1-2 min.

**Endpoints nuevos a probar**:

```
GET  /api/fintech/status
     → {"activa": false, "partner": "", ...}

GET  /api/admin/config/matriz_comercial
     → debería mostrar la matriz JSON completa
```

**Smoke test del motor**: crear una cotización de prueba, mandarla a un vivero,
aprobar y llegar a checkout. Los mensajes de WhatsApp deben mostrar el precio
comprador calculado según la categoría de cada item.

## 🔄 Ajuste manual pendiente para vos

Después de correr la migration, vas a querer **reclasificar algunos productos
manualmente** si tenés materas/sustrato/accesorios que quedaron mal categorizados:

```sql
-- Ejemplo: reclasificar productos específicos como materas
UPDATE inventario SET categoria_producto = 'materas' WHERE inventario_id IN (32, 33, ...);

-- O directo por vivero (Chaparro materas = vivero 14)
UPDATE inventario SET categoria_producto = 'materas' WHERE vivero_id = 14;
```

Esto lo podés hacer desde el SQL Editor o esperar a Fase 6 (dashboard admin
renovado) para tener UI.

## 🚨 Riesgos y rollback

- **Producción main no se toca** — deploy preview aislado
- **Rollback**: si algo falla, `git revert` del commit + rerun migration inverso
  (drop columns + delete clave configuracion_global)
- **Fintech inactiva por default** — no habilita pagos a plazos hasta que Elena
  explícitamente cambie `fintech_activa=true` desde admin

## 📊 Matemática esperada tras el deploy

Ejemplo real: viverista publica 1 matera a $10.000.

| Cliente | Cliente paga | Viverista recibe | ViveroOnline neto |
|---------|-------------|------------------|-------------------|
| B2C guest | $12.500 | $10.000 | $2.500 (25%) |
| B2B <5 SMLMV | $12.500 | $10.000 | $2.500 (25%) |
| B2B ≥5 SMLMV inmediato | $11.250 | $10.000 | $1.250 (15%) |
| B2B ≥5 SMLMV 90d Kontempo | $12.375 | $10.000 | $1.500 neto (15%) |

Viverista queda con $10.000 en TODOS los casos. ✓ Regla del Productor cumplida.
