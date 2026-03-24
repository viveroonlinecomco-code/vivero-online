-- ============================================================
-- ViveroOnline — Adiciones SQL para el MVP
-- Ejecutar en Supabase Dashboard → SQL Editor
-- DESPUÉS del schema_supabase.sql principal
-- ============================================================

-- 1. Columna nombre_vivero en viveristas (si no existe)
ALTER TABLE public.viveristas
  ADD COLUMN IF NOT EXISTS nombre_vivero TEXT;

-- Poblar con el nombre del viverista si está vacío
UPDATE public.viveristas
  SET nombre_vivero = nombre
  WHERE nombre_vivero IS NULL;

-- 2. Función para descontar stock al crear una transacción
CREATE OR REPLACE FUNCTION public.fn_descontar_stock(
    p_planta_id UUID,
    p_cantidad  INTEGER
)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    UPDATE public.catalogo_plantas
    SET stock_unidades = GREATEST(0, stock_unidades - p_cantidad),
        updated_at     = NOW()
    WHERE id = p_planta_id
      AND stock_unidades >= p_cantidad;
END;
$$;

-- 3. Vista enriquecida para el marketplace (plantas + datos del viverista)
CREATE OR REPLACE VIEW public.v_marketplace AS
SELECT
    p.id,
    p.viverista_id,
    p.nombre_comun,
    p.nombre_cientifico,
    p.descripcion,
    p.precio_cop,
    p.stock_unidades,
    p.imagen_url,
    p.vision_metadata,
    p.created_at,
    v.nombre          AS nombre_vendedor,
    v.nombre_vivero,
    v.municipio,
    v.telefono
FROM public.catalogo_plantas p
JOIN public.viveristas v ON v.id = p.viverista_id
WHERE p.stock_unidades > 0;

-- 4. Vista de transacciones enriquecida para el panel de viveristas
CREATE OR REPLACE VIEW public.v_transacciones_detalle AS
SELECT
    t.id,
    t.viverista_id,
    t.comprador_id,
    t.planta_id,
    t.cantidad,
    t.precio_unitario_cop,
    t.cantidad * t.precio_unitario_cop AS total_cop,
    t.estado,
    t.created_at,
    t.completada_at,
    p.nombre_comun    AS nombre_planta,
    p.imagen_url      AS imagen_planta,
    vend.nombre       AS nombre_vendedor,
    vend.nombre_vivero,
    comp.nombre       AS nombre_comprador,
    comp.telefono     AS telefono_comprador
FROM public.transacciones_b2b t
LEFT JOIN public.catalogo_plantas p  ON p.id = t.planta_id
LEFT JOIN public.viveristas vend     ON vend.id = t.viverista_id
LEFT JOIN public.viveristas comp     ON comp.id = t.comprador_id;

-- 5. Bucket de Supabase Storage para imágenes (ejecutar SOLO una vez)
-- Esto se hace desde Dashboard → Storage → New Bucket
-- Nombre: plant-images
-- Public: SÍ (para que las URLs funcionen sin autenticación)
-- Puedes también ejecutarlo via API:
-- INSERT INTO storage.buckets (id, name, public) VALUES ('plant-images', 'plant-images', true)
-- ON CONFLICT DO NOTHING;

-- 6. Política de Storage: cualquiera puede leer, solo el backend escribe
-- (El backend usa SERVICE_KEY que bypasea RLS)
-- Si quieres restringir lectura, cambia public a false y agrega policies.

-- 7. Índice para búsqueda de texto en el catálogo
CREATE INDEX IF NOT EXISTS idx_plantas_nombre_texto
  ON public.catalogo_plantas
  USING gin(to_tsvector('spanish', nombre_comun || ' ' || COALESCE(nombre_cientifico, '')));

-- ─── VERIFICACIÓN ────────────────────────────────────────────────────────────
SELECT
  'viveristas'      AS tabla, COUNT(*) AS registros FROM public.viveristas
UNION ALL
SELECT
  'catalogo_plantas', COUNT(*) FROM public.catalogo_plantas
UNION ALL
SELECT
  'transacciones_b2b', COUNT(*) FROM public.transacciones_b2b;
