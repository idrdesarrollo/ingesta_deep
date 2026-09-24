-- =============================================================================
-- Migración 003: cambiar convocatorias.embedding de vector(1536) a vector(1024)
--
-- Motivo: se pasa de embeddings de OpenAI (1536 dim) a BGE-M3 (1024 dim),
--         modelo local y multilingüe. La columna estaba vacía, así que el
--         cambio no pierde datos. El índice HNSW se RECREA tras el backfill
--         (ver scripts/backfill_embeddings.py), no en esta migración.
--
-- Ejecutar: psql -U postgres -d oceano_azul -f migrations/003_embedding_bge_m3_1024.sql
-- Antes:    backup -> pg_dump -U postgres oceano_azul > backup.sql
-- =============================================================================

BEGIN;

-- 1. El índice HNSW depende del tipo de la columna: hay que eliminarlo antes.
DROP INDEX IF EXISTS idx_conv_embedding;

-- 2. Limpiar cualquier vector previo (serían de 1536 dim / OpenAI, no válidos
--    para BGE-M3). Si la columna ya estaba vacía, es un no-op.
UPDATE convocatorias SET embedding = NULL WHERE embedding IS NOT NULL;

-- 3. Cambiar la dimensión de la columna.
ALTER TABLE convocatorias
    ALTER COLUMN embedding TYPE vector(1024);

-- 4. Verificación
DO $$
DECLARE
    v_tipo TEXT;
BEGIN
    SELECT format_type(atttypid, atttypmod) INTO v_tipo
    FROM pg_attribute
    WHERE attrelid = 'convocatorias'::regclass
      AND attname  = 'embedding'
      AND NOT attisdropped;

    IF v_tipo = 'vector(1024)' THEN
        RAISE NOTICE 'OK — convocatorias.embedding es ahora %', v_tipo;
    ELSE
        RAISE EXCEPTION 'ERROR — el tipo es % (se esperaba vector(1024)). Revisar migración.', v_tipo;
    END IF;
END;
$$;

COMMIT;

-- =============================================================================
-- SIGUIENTE PASO (no incluido en esta migración):
--   1) Rellenar embeddings:   python scripts/backfill_embeddings.py
--   2) Recrear el índice HNSW (con la tabla ya poblada):
--        CREATE INDEX idx_conv_embedding
--            ON convocatorias USING hnsw (embedding vector_cosine_ops)
--            WITH (m = 16, ef_construction = 64);
-- =============================================================================
