-- migrations/006_documentos_upsert.sql
BEGIN;

-- Limpia posibles duplicados previos antes de crear el índice único
DELETE FROM documentos a USING documentos b
WHERE a.id > b.id AND a.convocatoria_id = b.convocatoria_id AND a.url = b.url;

CREATE UNIQUE INDEX IF NOT EXISTS idx_docs_conv_url
    ON documentos (convocatoria_id, url);

COMMIT;