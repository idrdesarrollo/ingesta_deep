-- =============================================================================
-- Migración 007: desacoplar V9 (historial de recurrencia) de la ingesta
--
-- Motivo: buscar_historial_previo() hacía 1-3 llamadas HTTP a BDNS por
--         convocatoria DENTRO de scorer.calcular_y_guardar(), que se llama
--         una vez por convocatoria en datBBDD.databaseInsert(). Con
--         pageSize=10000 en "Traer todas las convocatorias" eso multiplica
--         el tiempo de ingesta y arriesga que BDNS bloquee la IP.
--
--         A partir de esta migración, la ingesta guarda V9 provisional
--         (=1, conservador) y marca v9_estado='pendiente'/'sin_organo'.
--         Un proceso aparte (resolverHistorial.py), con su propio horario y
--         throttling, resuelve el historial real por lotes y completa la
--         fila. historial_cache evita repetir la búsqueda en BDNS para
--         convocatorias del mismo organismo + programa (título sin año).
--
-- Ejecutar: psql -U postgres -d oceano_azul -f migrations/007_historial_v9_desacoplado.sql
-- Antes:    backup -> pg_dump -U postgres oceano_azul > backup.sql
-- Nota:     escrita para ser re-ejecutable (IF NOT EXISTS) por si el cambio
--           ya se aplicó a mano en algún entorno.
-- =============================================================================

BEGIN;

-- 1. Cache de historial por organismo + título normalizado (sin año, sin
--    tildes/puntuación — ver keywords/historial.normalizar_titulo()).
CREATE TABLE IF NOT EXISTS historial_cache (
    id                  SERIAL PRIMARY KEY,
    organo_id           INTEGER NOT NULL REFERENCES organos(id),
    titulo_normalizado  TEXT NOT NULL,
    n_similares         INTEGER NOT NULL DEFAULT 0,
    anios_publicacion   INTEGER[] NOT NULL DEFAULT '{}',
    tasa_ejecucion      NUMERIC(6, 2),
    calculado_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organo_id, titulo_normalizado)
);

-- 2. Estado de resolución de V9 en scoring_results.
ALTER TABLE scoring_results
    ADD COLUMN IF NOT EXISTS v9_estado TEXT NOT NULL DEFAULT 'pendiente',
    ADD COLUMN IF NOT EXISTS v9_intentos SMALLINT NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scoring_results_v9_estado_check'
    ) THEN
        ALTER TABLE scoring_results
            ADD CONSTRAINT scoring_results_v9_estado_check
                CHECK (v9_estado IN ('pendiente', 'calculado', 'sin_organo', 'error'));
    END IF;
END;
$$;

-- 3. Índice para que resolverHistorial.py encuentre su cola sin escanear
--    toda la tabla (filtra por vigente + pendiente/error con reintentos
--    disponibles).
CREATE INDEX IF NOT EXISTS idx_scoring_v9_pendiente
    ON scoring_results (created_at)
    WHERE es_vigente = TRUE AND v9_estado IN ('pendiente', 'error');

-- 4. Verificación
DO $$
DECLARE
    v_tabla_cache BOOLEAN;
    v_columnas    INTEGER;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'historial_cache'
    ) INTO v_tabla_cache;

    SELECT COUNT(*) INTO v_columnas
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'scoring_results'
      AND column_name IN ('v9_estado', 'v9_intentos');

    IF v_tabla_cache AND v_columnas = 2 THEN
        RAISE NOTICE 'OK — historial_cache creada y scoring_results.v9_estado/v9_intentos presentes';
    ELSE
        RAISE EXCEPTION 'ERROR — migración 007 incompleta (historial_cache=%, columnas v9=%)', v_tabla_cache, v_columnas;
    END IF;
END;
$$;

COMMIT;
