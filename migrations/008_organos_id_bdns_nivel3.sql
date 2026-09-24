-- =============================================================================
-- Migración 008: persistir el ID numérico BDNS del órgano en organos
--
-- Motivo: id_organo_nivel3 (el ID que usa BDNS internamente para el órgano,
--         necesario para /convocatorias/busqueda?organos=X en V9) se
--         resolvía por texto (nivel3_desc == df_organos.organo) EN CADA
--         convocatoria, y df_organos se reconstruye desde cero (4 llamadas
--         a /organos) cada vez que arranca el robot. Con el resolver de V9
--         corriendo como proceso aparte (resolverHistorial.py), repetir esa
--         resolución frágil por texto ahí también habría sido un segundo
--         punto de fallo silencioso (mayúsculas/tildes/espacios distintos
--         entre el endpoint de convocatorias y el de organismos).
--
--         Con esta columna, la resolución por texto se hace UNA sola vez
--         por órgano -en datBBDD.buscar_organo_id(), que ya tiene
--         df_organos cargado- y se persiste. resolverHistorial.py hace un
--         JOIN simple, sin volver a tocar texto ni llamar a /organos.
--
-- Ejecutar: psql -U postgres -d oceano_azul -f migrations/008_organos_id_bdns_nivel3.sql
-- Antes:    backup -> pg_dump -U postgres oceano_azul > backup.sql
-- Después:  hay que rellenar esta columna para los órganos ya existentes
--           (creados antes de esta migración) -> scripts/backfill_id_bdns_nivel3.py
--           Hasta que se rellene, resolverHistorial.py marcará esas filas
--           como v9_estado='sin_organo' (no se pierden, pero no se
--           resuelven hasta el backfill).
-- Nota:     escrita para ser re-ejecutable (IF NOT EXISTS).
-- =============================================================================

BEGIN;

ALTER TABLE organos
    ADD COLUMN IF NOT EXISTS id_bdns_nivel3 INTEGER;

-- 2. Verificación
DO $$
DECLARE
    v_existe BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'organos'
          AND column_name = 'id_bdns_nivel3'
    ) INTO v_existe;

    IF v_existe THEN
        RAISE NOTICE 'OK — organos.id_bdns_nivel3 presente';
    ELSE
        RAISE EXCEPTION 'ERROR — migración 008 incompleta (falta organos.id_bdns_nivel3)';
    END IF;
END;
$$;

COMMIT;
