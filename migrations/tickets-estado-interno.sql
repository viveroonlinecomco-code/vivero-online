-- Migration: Agregar campos para Opción B HYBRID (18 ago 2026)
-- Objetivo: Tracking interno + tipo derivación + fecha confirmación

ALTER TABLE tickets_soporte 
ADD COLUMN IF NOT EXISTS estado_interno VARCHAR(50) DEFAULT 'nuevo';

ALTER TABLE tickets_soporte
ADD COLUMN IF NOT EXISTS tipo_derivacion VARCHAR(50);

ALTER TABLE tickets_soporte
ADD COLUMN IF NOT EXISTS fecha_confirmacion TIMESTAMP NULL;

-- Índices para búsquedas rápidas
CREATE INDEX IF NOT EXISTS idx_tickets_estado_interno 
ON tickets_soporte(estado_interno);

CREATE INDEX IF NOT EXISTS idx_tickets_tipo_derivacion 
ON tickets_soporte(tipo_derivacion);

-- Verificación (ejecutar después de migration)
-- SELECT column_name, data_type FROM information_schema.columns 
-- WHERE table_name = 'tickets_soporte' AND column_name IN ('estado_interno', 'tipo_derivacion', 'fecha_confirmacion');
