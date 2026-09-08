-- Migración 0010 - Agregar campos para payouts 60/40 y reclamos
-- Rama: feat/payouts-60-40
-- Archivo: migrations/0010_add_payouts_60_40_campos.sql
-- Ejecutar en: Supabase SQL Editor (dashboard)
--
-- CAMBIOS:
-- 1. tickets_soporte: agregar campos para reclamos tipo RECLAMO
-- 2. entregas: agregar campos para fotos y timestamps
-- 3. Crear índices para queries rápidas
--
-- EJECUCIÓN:
-- 1. Copiar este archivo
-- 2. Entrar a Supabase Dashboard
-- 3. SQL Editor → Paste → Run
-- 4. Verificar que todo paso sin errores
-- 5. Commit a GitHub con comentario "migrations: add payouts 60/40 fields"

-- ═══════════════════════════════════════════════════════════════
-- 1. AGREGAR CAMPOS A tickets_soporte
-- ═══════════════════════════════════════════════════════════════

-- Campo: tipo de ticket (RECLAMO o CONSULTA)
ALTER TABLE public.tickets_soporte
ADD COLUMN IF NOT EXISTS ticket_type VARCHAR(50) DEFAULT 'CONSULTA',
ADD CONSTRAINT chk_ticket_type CHECK (ticket_type IN ('RECLAMO', 'CONSULTA'));

COMMENT ON COLUMN public.tickets_soporte.ticket_type IS
  'Tipo de ticket: RECLAMO (vinculado a entrega) o CONSULTA (soporte general)';


-- Campo: vinculación a entrega
ALTER TABLE public.tickets_soporte
ADD COLUMN IF NOT EXISTS entrega_id INT,
ADD CONSTRAINT fk_tickets_soporte_entregas FOREIGN KEY (entrega_id)
  REFERENCES public.entregas(entrega_id)
  ON DELETE SET NULL;

COMMENT ON COLUMN public.tickets_soporte.entrega_id IS
  'Referencia a entrega (solo si ticket_type=RECLAMO)';


-- Campo: vinculación a pago
ALTER TABLE public.tickets_soporte
ADD COLUMN IF NOT EXISTS pago_id INT,
ADD CONSTRAINT fk_tickets_soporte_pagos FOREIGN KEY (pago_id)
  REFERENCES public.pagos(pago_id)
  ON DELETE SET NULL;

COMMENT ON COLUMN public.tickets_soporte.pago_id IS
  'Referencia a pago (solo si ticket_type=RECLAMO)';


-- Campo: validación con epayco_id
ALTER TABLE public.tickets_soporte
ADD COLUMN IF NOT EXISTS epayco_id VARCHAR(100);

COMMENT ON COLUMN public.tickets_soporte.epayco_id IS
  'ID de ePayco para validar que ticket corresponde a pago real';


-- Campo: motivo del reclamo
ALTER TABLE public.tickets_soporte
ADD COLUMN IF NOT EXISTS motivo_reclamo VARCHAR(100);

COMMENT ON COLUMN public.tickets_soporte.motivo_reclamo IS
  'Motivo del reclamo (ej: dañado, incorrecto, marchito)';


-- ═══════════════════════════════════════════════════════════════
-- 2. AGREGAR CAMPOS A entregas
-- ═══════════════════════════════════════════════════════════════

-- Campo: foto del despacho (del conductor)
ALTER TABLE public.entregas
ADD COLUMN IF NOT EXISTS foto_despacho TEXT;

COMMENT ON COLUMN public.entregas.foto_despacho IS
  'URL o referencia a foto del conductor en despacho';


-- Campo: foto de la entrega
ALTER TABLE public.entregas
ADD COLUMN IF NOT EXISTS foto_entrega TEXT;

COMMENT ON COLUMN public.entregas.foto_entrega IS
  'URL o referencia a foto de entrega en destino';


-- Campo: timestamp de despacho (para trigger Payout 60%)
ALTER TABLE public.entregas
ADD COLUMN IF NOT EXISTS timestamp_despacho TIMESTAMP WITH TIME ZONE;

COMMENT ON COLUMN public.entregas.timestamp_despacho IS
  'Timestamp exacto cuando se confirma despacho (trigger para Payout 60%)';


-- Campo: timestamp de entrega (inicio contador 24h)
ALTER TABLE public.entregas
ADD COLUMN IF NOT EXISTS timestamp_entrega TIMESTAMP WITH TIME ZONE;

COMMENT ON COLUMN public.entregas.timestamp_entrega IS
  'Timestamp exacto cuando se confirma entrega (inicia contador 24h automático)';


-- Campo: cuándo vence la garantía 24h
ALTER TABLE public.entregas
ADD COLUMN IF NOT EXISTS garantia_inicia TIMESTAMP WITH TIME ZONE;

COMMENT ON COLUMN public.entregas.garantia_inicia IS
  'Timestamp cuando inicia garantía 24h (= timestamp_entrega, usado por CRON)';


-- ═══════════════════════════════════════════════════════════════
-- 3. CREAR ÍNDICES PARA QUERIES RÁPIDAS
-- ═══════════════════════════════════════════════════════════════

-- Índice: tickets_soporte por tipo y estado (para CRON)
CREATE INDEX IF NOT EXISTS idx_tickets_soporte_tipo_estado
  ON public.tickets_soporte (ticket_type, estado)
  WHERE ticket_type = 'RECLAMO' AND estado = 'abierto';

COMMENT ON INDEX public.idx_tickets_soporte_tipo_estado IS
  'Índice para búsqueda rápida de reclamos abiertos por CRON';


-- Índice: entregas por timestamp (para CRON 24h)
CREATE INDEX IF NOT EXISTS idx_entregas_timestamp_entrega
  ON public.entregas (timestamp_entrega, estado_entrega)
  WHERE estado_entrega = 'entregado';

COMMENT ON INDEX public.idx_entregas_timestamp_entrega IS
  'Índice para búsqueda rápida de entregas para verificar 24h';


-- Índice: entregas por cotizacion (para búsquedas)
CREATE INDEX IF NOT EXISTS idx_entregas_cotizacion_id
  ON public.entregas (cotizacion_id);

COMMENT ON INDEX public.idx_entregas_cotizacion_id IS
  'Índice para búsqueda rápida de entrega por cotización';


-- ═══════════════════════════════════════════════════════════════
-- 4. ACTUALIZAR RLS (Row Level Security) SI ES NECESARIO
-- ═══════════════════════════════════════════════════════════════

-- Nota: Si RLS está activo, puede ser necesario crear políticas
-- para los nuevos campos. Esto dependerá de tu implementación
-- específica de autenticación y roles.

-- Ejemplo (si tienes table policies):
-- CREATE POLICY "tickets_reclamo_select_creador" ON public.tickets_soporte
--   FOR SELECT USING (auth.uid() = cliente_id OR is_admin());

-- ═══════════════════════════════════════════════════════════════
-- 5. VERIFICACIÓN POST-MIGRACIÓN
-- ═══════════════════════════════════════════════════════════════

-- Ejecutar después de aplicar migración para verificar:
-- SELECT column_name, data_type, is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'tickets_soporte'
-- ORDER BY ordinal_position;

-- SELECT column_name, data_type, is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'entregas'
-- ORDER BY ordinal_position;
