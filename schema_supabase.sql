-- ============================================================
-- ViveroOnline — Schema Supabase + RLS + Vistas Data Flywheel
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- ============================================================

-- ─── EXTENSIONES ────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";  -- para búsqueda semántica futura

-- ─── TABLA: viveristas ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.viveristas (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre          TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    telefono        TEXT,
    municipio       TEXT,                    -- 'Cajicá', 'Zipaquirá', etc.
    sabana_zone     TEXT,                    -- zona geográfica Sabana de Bogotá
    activo          BOOLEAN DEFAULT FALSE,   -- true = ha hecho al menos 1 transacción
    onboarded_at    TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─── TABLA: catalogo_plantas ────────────────────────────────
CREATE TABLE IF NOT EXISTS public.catalogo_plantas (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    viverista_id    UUID REFERENCES public.viveristas(id) ON DELETE CASCADE,
    nombre_comun    TEXT NOT NULL,
    nombre_cientifico TEXT,
    descripcion     TEXT,
    precio_cop      NUMERIC(12, 2),
    stock_unidades  INTEGER DEFAULT 0,
    imagen_url      TEXT,                    -- Supabase Storage URL
    vision_metadata JSONB,                   -- resultado crudo de Gemini Vision
    embedding       vector(768),             -- para búsqueda semántica futura
    verificado      BOOLEAN DEFAULT FALSE,   -- revisado manualmente
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─── TABLA: transacciones_b2b ───────────────────────────────
CREATE TABLE IF NOT EXISTS public.transacciones_b2b (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    viverista_id        UUID REFERENCES public.viveristas(id),
    comprador_id        UUID REFERENCES public.viveristas(id),  -- puede ser otro viverista
    planta_id           UUID REFERENCES public.catalogo_plantas(id),
    cantidad            INTEGER NOT NULL,
    precio_unitario_cop NUMERIC(12, 2) NOT NULL,
    total_cop           NUMERIC(14, 2) GENERATED ALWAYS AS (cantidad * precio_unitario_cop) STORED,
    estado              TEXT DEFAULT 'pendiente' CHECK (estado IN ('pendiente','confirmada','enviada','completada','cancelada')),
    notas               TEXT,
    completada_at       TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ─── TABLA: eventos_agente ──────────────────────────────────
-- Rastrea cada acción del agente LangGraph para el flywheel
CREATE TABLE IF NOT EXISTS public.eventos_agente (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tipo        TEXT NOT NULL,  -- 'vision_scan', 'catalog_add', 'search', 'chat'
    payload     JSONB,
    viverista_id UUID REFERENCES public.viveristas(id),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─── ÍNDICES DE RENDIMIENTO ──────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_plantas_viverista ON public.catalogo_plantas(viverista_id);
CREATE INDEX IF NOT EXISTS idx_transacciones_estado ON public.transacciones_b2b(estado);
CREATE INDEX IF NOT EXISTS idx_transacciones_fecha ON public.transacciones_b2b(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_eventos_tipo ON public.eventos_agente(tipo, created_at DESC);

-- ─── ROW LEVEL SECURITY (RLS) ────────────────────────────────
ALTER TABLE public.viveristas        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.catalogo_plantas  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transacciones_b2b ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.eventos_agente    ENABLE ROW LEVEL SECURITY;

-- Viveristas solo ven su propio perfil
CREATE POLICY "viverista_own_profile" ON public.viveristas
    FOR ALL USING (auth.uid()::text = id::text);

-- Catálogo: cualquier viverista autenticado puede leer, solo el dueño puede escribir
CREATE POLICY "catalog_read_all" ON public.catalogo_plantas
    FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "catalog_write_own" ON public.catalogo_plantas
    FOR ALL USING (auth.uid()::text = viverista_id::text);

-- Transacciones: solo las partes involucradas
CREATE POLICY "transactions_own" ON public.transacciones_b2b
    FOR ALL USING (
        auth.uid()::text = viverista_id::text OR
        auth.uid()::text = comprador_id::text
    );

-- Service role (backend) puede todo — para el agente LangGraph
-- Usa SUPABASE_SERVICE_KEY en el backend, nunca la anon key

-- ─── VISTAS DATA FLYWHEEL (métricas para el inversor) ────────

-- Vista 1: KPIs de alto nivel
CREATE OR REPLACE VIEW public.v_flywheel_kpis AS
SELECT
    -- Viveristas
    COUNT(DISTINCT v.id)                                        AS total_viveristas_registrados,
    COUNT(DISTINCT v.id) FILTER (WHERE v.activo = TRUE)         AS viveristas_activos,
    COUNT(DISTINCT v.id) FILTER (
        WHERE v.onboarded_at >= NOW() - INTERVAL '30 days'
    )                                                           AS nuevos_viveristas_30d,

    -- Catálogo
    COUNT(DISTINCT p.id)                                        AS total_plantas_catalogo,
    COUNT(DISTINCT p.id) FILTER (
        WHERE p.created_at >= NOW() - INTERVAL '30 days'
    )                                                           AS plantas_nuevas_30d,
    COUNT(DISTINCT p.viverista_id)                              AS viveristas_con_catalogo,

    -- Transacciones
    COUNT(DISTINCT t.id) FILTER (WHERE t.estado = 'completada') AS transacciones_completadas,
    COALESCE(SUM(t.total_cop) FILTER (WHERE t.estado = 'completada'), 0) AS gmv_total_cop,
    COUNT(DISTINCT t.id) FILTER (
        WHERE t.estado = 'completada'
        AND t.completada_at >= NOW() - INTERVAL '30 days'
    )                                                           AS transacciones_completadas_30d,

    -- Tasa de activación
    ROUND(
        COUNT(DISTINCT v.id) FILTER (WHERE v.activo = TRUE)::NUMERIC /
        NULLIF(COUNT(DISTINCT v.id), 0) * 100, 1
    )                                                           AS tasa_activacion_pct

FROM public.viveristas v
LEFT JOIN public.catalogo_plantas p  ON p.viverista_id = v.id
LEFT JOIN public.transacciones_b2b t ON t.viverista_id = v.id;

-- Vista 2: Crecimiento semanal (para gráfica de tracción)
CREATE OR REPLACE VIEW public.v_crecimiento_semanal AS
SELECT
    DATE_TRUNC('week', serie.semana)::DATE  AS semana,
    COUNT(DISTINCT v.id)                    AS viveristas_acumulados,
    COUNT(DISTINCT p.id)                    AS plantas_acumuladas,
    COUNT(DISTINCT t.id) FILTER (WHERE t.estado = 'completada') AS transacciones_acumuladas
FROM generate_series(
    DATE_TRUNC('week', (SELECT MIN(created_at) FROM public.viveristas)),
    NOW(),
    '1 week'::INTERVAL
) AS serie(semana)
LEFT JOIN public.viveristas v
    ON v.created_at < serie.semana + INTERVAL '1 week'
LEFT JOIN public.catalogo_plantas p
    ON p.created_at < serie.semana + INTERVAL '1 week'
LEFT JOIN public.transacciones_b2b t
    ON t.created_at < serie.semana + INTERVAL '1 week'
GROUP BY 1
ORDER BY 1;

-- Vista 3: Top viveristas por actividad (para ranking en dashboard)
CREATE OR REPLACE VIEW public.v_top_viveristas AS
SELECT
    v.nombre,
    v.municipio,
    COUNT(DISTINCT p.id)                AS plantas_en_catalogo,
    COUNT(DISTINCT t.id) FILTER (WHERE t.estado = 'completada') AS ventas_completadas,
    COALESCE(SUM(t.total_cop) FILTER (WHERE t.estado = 'completada'), 0) AS ingresos_cop,
    MAX(t.completada_at)                AS ultima_venta
FROM public.viveristas v
LEFT JOIN public.catalogo_plantas p  ON p.viverista_id = v.id
LEFT JOIN public.transacciones_b2b t ON t.viverista_id = v.id
GROUP BY v.id, v.nombre, v.municipio
ORDER BY ventas_completadas DESC, plantas_en_catalogo DESC;

-- ─── FUNCIÓN: activar viverista al completar primera transacción ─
CREATE OR REPLACE FUNCTION public.fn_activar_viverista()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    IF NEW.estado = 'completada' AND OLD.estado != 'completada' THEN
        UPDATE public.viveristas
        SET activo = TRUE, last_seen_at = NOW()
        WHERE id = NEW.viverista_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_activar_viverista
    AFTER UPDATE ON public.transacciones_b2b
    FOR EACH ROW EXECUTE FUNCTION public.fn_activar_viverista();
