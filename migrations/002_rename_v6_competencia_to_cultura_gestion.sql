-- =============================================================================
-- Migración 002: renombrar v6_competencia → v6_cultura_gestion en scoring_results
--
-- Motivo: V6 ya no mide la competencia entre solicitantes sino la cultura de
--         gestión de ayudas del sector (cuanto menos cultura, mayor oportunidad).
--
-- Ejecutar: psql -U postgres -d oceano_azul -f migrations/002_rename_v6_competencia_to_cultura_gestion.sql
-- =============================================================================

BEGIN;

-- 1. Renombrar la columna
ALTER TABLE scoring_results
    RENAME COLUMN v6_competencia TO v6_cultura_gestion;

-- 2. Renombrar el CHECK constraint generado automáticamente
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name   = 'scoring_results'
          AND constraint_name = 'scoring_results_v6_competencia_check'
    ) THEN
        ALTER TABLE scoring_results
            RENAME CONSTRAINT scoring_results_v6_competencia_check
                           TO scoring_results_v6_cultura_gestion_check;
    END IF;
END;
$$;

-- 3. Verificación
DO $$
DECLARE v_col TEXT;
BEGIN
    SELECT column_name INTO v_col
    FROM information_schema.columns
    WHERE table_name = 'scoring_results' AND column_name = 'v6_cultura_gestion';

    IF v_col IS NOT NULL THEN
        RAISE NOTICE 'OK — columna v6_cultura_gestion existe en scoring_results';
    ELSE
        RAISE EXCEPTION 'ERROR — la columna v6_cultura_gestion NO existe. Revisar migración.';
    END IF;
END;
$$;

COMMIT;
