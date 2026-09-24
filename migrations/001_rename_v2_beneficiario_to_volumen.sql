-- =============================================================================
-- Migración 001: renombrar v2_beneficiario → v2_volumen en scoring_results
--
-- Motivo: V2 ya no mide el encaje con el perfil de la empresa sino el volumen
--         de beneficiarios potenciales (tipo BDNS + alcance geográfico).
--
-- Ejecutar: psql -U postgres -d oceano_azul -f migrations/001_rename_v2_beneficiario_to_volumen.sql
-- =============================================================================

BEGIN;

-- 1. Renombrar la columna
ALTER TABLE scoring_results
    RENAME COLUMN v2_beneficiario TO v2_volumen;

-- 2. Renombrar el CHECK constraint generado automáticamente
--    (el nombre por defecto en PostgreSQL es <tabla>_<columna>_check)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'scoring_results'
          AND constraint_name = 'scoring_results_v2_beneficiario_check'
    ) THEN
        ALTER TABLE scoring_results
            RENAME CONSTRAINT scoring_results_v2_beneficiario_check
                           TO scoring_results_v2_volumen_check;
    END IF;
END;
$$;

-- 3. Verificación
DO $$
DECLARE
    v_col TEXT;
BEGIN
    SELECT column_name INTO v_col
    FROM information_schema.columns
    WHERE table_name = 'scoring_results' AND column_name = 'v2_volumen';

    IF v_col IS NOT NULL THEN
        RAISE NOTICE 'OK — columna v2_volumen existe en scoring_results';
    ELSE
        RAISE EXCEPTION 'ERROR — la columna v2_volumen NO existe. Revisar la migración.';
    END IF;
END;
$$;

COMMIT;
