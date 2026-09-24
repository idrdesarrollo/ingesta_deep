-- =============================================================================
-- Océano Azul — Esquema PostgreSQL completo
-- Ejecutar con: psql -U postgres -d oceano_azul -f schema_init.sql
-- Requiere PostgreSQL 15+ con extensiones pgvector y uuid-ossp
-- =============================================================================

-- Crear base de datos (ejecutar como superusuario si no existe):
-- CREATE DATABASE oceano_azul;
-- \c oceano_azul

-- -----------------------------------------------------------------------------
-- Extensiones
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;          -- pgvector para embeddings

-- =============================================================================
-- 1. organos — Catálogo jerárquico de organismos BDNS (DIR3)
-- =============================================================================
CREATE TABLE IF NOT EXISTS organos (
    id          SERIAL PRIMARY KEY,
    codigo_dir3 TEXT UNIQUE,
    nivel1      TEXT NOT NULL,
    nivel2      TEXT,
    nivel3      TEXT,
    tipo        SMALLINT NOT NULL CHECK (tipo BETWEEN 1 AND 4),
    -- tipo: 1=Entidad Local, 2=Comunidad Autónoma, 3=Administración del Estado, 4=Otros

    -- ID numérico que BDNS usa internamente para este órgano (no tiene
    -- relación con `id`). Se resuelve por texto UNA vez en
    -- datBBDD.buscar_organo_id() y se persiste aquí para que
    -- resolverHistorial.py (V9) no tenga que repetir esa resolución frágil
    -- ni volver a llamar a /organos. NULL si no se pudo resolver (entonces
    -- V9 se marca 'sin_organo'). Añadido por migrations/008.
    id_bdns_nivel3 INTEGER
);

CREATE INDEX IF NOT EXISTS idx_organos_nivel1 ON organos (nivel1);
CREATE INDEX IF NOT EXISTS idx_organos_tipo   ON organos (tipo);

-- =============================================================================
-- 2. catalogo_items — Catálogos pequeños unificados
--    tipo: 'sector' | 'beneficiario' | 'instrumento' | 'finalidad' | 'reglamento' | 'objetivo'
-- =============================================================================
CREATE TABLE IF NOT EXISTS catalogo_items (
    id     SERIAL PRIMARY KEY,
    tipo   TEXT NOT NULL,
    codigo TEXT NOT NULL,
    nombre TEXT NOT NULL,
    UNIQUE (tipo, codigo)
);

CREATE INDEX IF NOT EXISTS idx_catalogo_tipo ON catalogo_items (tipo);

-- =============================================================================
-- 3. convocatorias — Tabla principal
-- =============================================================================
CREATE TABLE IF NOT EXISTS convocatorias (
    id                    BIGINT PRIMARY KEY,           -- id numérico BDNS
    codigo_bdns           TEXT UNIQUE NOT NULL,
    numero_convocatoria   TEXT,
    organo_id             INTEGER REFERENCES organos(id),
    descripcion           TEXT NOT NULL,
    descripcion_leng      TEXT,
    tipo_convocatoria     TEXT,
    sede_electronica      TEXT,
    presupuesto_total     NUMERIC(15, 2),
    fecha_recepcion       DATE NOT NULL,
    fecha_inicio_solicitud DATE,
    fecha_fin_solicitud   DATE,
    text_inicio           TEXT,
    text_fin              TEXT,
    abierto_indefinido    BOOLEAN NOT NULL DEFAULT FALSE,
    estado_vigencia       TEXT NOT NULL DEFAULT 'SinDatos'
                              CHECK (estado_vigencia IN
                                  ('Abierta', 'Cerrada', 'Pendiente', 'Indefinida', 'SinDatos')),
    bases_reguladoras     TEXT,
    url_bases_reguladoras TEXT,
    publica_diario_oficial BOOLEAN DEFAULT FALSE,
    ayuda_estado          TEXT,
    finalidad_id          INTEGER REFERENCES catalogo_items(id),
    reglamento_id         INTEGER REFERENCES catalogo_items(id),

    -- Arrays M:N (eliminan 5 tablas de unión)
    sectores_ids          INTEGER[] DEFAULT '{}',
    regiones_ids          INTEGER[] DEFAULT '{}',
    beneficiarios_ids     INTEGER[] DEFAULT '{}',
    instrumentos_ids      INTEGER[] DEFAULT '{}',
    objetivos_ids         INTEGER[] DEFAULT '{}',

    -- Score desnormalizado para dashboard sin JOIN
    score_pct             NUMERIC(5, 2),
    nivel_scoring         TEXT CHECK (nivel_scoring IN
                              ('azul', 'oportunidad', 'revisar', 'descartar')),

    -- Búsqueda semántica: BGE-M3 (1024 dim), normalizado -> coseno.
    -- Cambiado desde VECTOR(1536) por migrations/003. Mantener sincronizado.
    embedding             VECTOR(1024),

    procesada             BOOLEAN NOT NULL DEFAULT FALSE,
    fuente                TEXT NOT NULL DEFAULT 'bdns',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Listado principal del dashboard (index-only scan, solo procesadas)
CREATE INDEX IF NOT EXISTS idx_conv_dashboard
    ON convocatorias (estado_vigencia, score_pct DESC NULLS LAST)
    WHERE procesada = TRUE;

-- Job diario: actualizar convocatorias con fecha_fin vencida
CREATE INDEX IF NOT EXISTS idx_conv_abiertas
    ON convocatorias (fecha_fin_solicitud)
    WHERE estado_vigencia = 'Abierta';

-- Búsqueda full-text en español sobre descripción
CREATE INDEX IF NOT EXISTS idx_conv_fts
    ON convocatorias USING GIN (to_tsvector('spanish', descripcion));

-- Arrays: filtro 'convocatorias del sector/región/beneficiario X'
CREATE INDEX IF NOT EXISTS idx_conv_sectores   ON convocatorias USING GIN (sectores_ids);
CREATE INDEX IF NOT EXISTS idx_conv_regiones   ON convocatorias USING GIN (regiones_ids);
CREATE INDEX IF NOT EXISTS idx_conv_benef      ON convocatorias USING GIN (beneficiarios_ids);

-- Búsqueda semántica HNSW (pgvector) — válido para <1M filas
CREATE INDEX IF NOT EXISTS idx_conv_embedding
    ON convocatorias USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- =============================================================================
-- 4. documentos — Adjuntos de cada convocatoria
-- =============================================================================
CREATE TABLE IF NOT EXISTS documentos (
    id               SERIAL PRIMARY KEY,
    convocatoria_id  BIGINT NOT NULL REFERENCES convocatorias(id) ON DELETE CASCADE,
    nombre           TEXT NOT NULL,
    url              TEXT,
    fecha_mod        DATE,
    fecha_pub        DATE
);

CREATE INDEX IF NOT EXISTS idx_docs_conv ON documentos (convocatoria_id);
-- Requerido por el ON CONFLICT de datBBDD._update_documentos: permite reinsertar
-- sin borrar, de modo que los documentos sin cambios conservan su id.
-- Añadido por migrations/006. Mantener sincronizado.
CREATE UNIQUE INDEX IF NOT EXISTS idx_docs_conv_url ON documentos (convocatoria_id, url);

-- =============================================================================
-- 5. scoring_results — Historial de cálculos de scoring V1-V9
-- =============================================================================
CREATE TABLE IF NOT EXISTS scoring_results (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    convocatoria_id  BIGINT NOT NULL REFERENCES convocatorias(id),
    v1_tramitacion_estandarizada SMALLINT CHECK (v1_tramitacion_estandarizada BETWEEN 1 AND 5),
    v2_volumen       SMALLINT CHECK (v2_volumen       BETWEEN 1 AND 5),
    v3_cuantia       SMALLINT CHECK (v3_cuantia       BETWEEN 1 AND 5),
    v4_intermediarios SMALLINT CHECK (v4_intermediarios BETWEEN 1 AND 5),
    v5_presupuesto_recurrente SMALLINT CHECK (v5_presupuesto_recurrente BETWEEN 1 AND 5),
    v6_cultura_gestion SMALLINT CHECK (v6_cultura_gestion BETWEEN 1 AND 5),
    v7_req_tecnicos  SMALLINT CHECK (v7_req_tecnicos  BETWEEN 1 AND 5),
    v8_plazo_cobro   SMALLINT CHECK (v8_plazo_cobro   BETWEEN 1 AND 5),
    v9_historial     SMALLINT CHECK (v9_historial     BETWEEN 1 AND 5),
    score_raw        NUMERIC(8, 2),
    score_pct        NUMERIC(5, 2),
    nivel_asignado   TEXT CHECK (nivel_asignado IN
                         ('azul', 'oportunidad', 'revisar', 'descartar')),
    pesos_snapshot   JSONB NOT NULL,
    justificaciones  JSONB,
    es_vigente       BOOLEAN NOT NULL DEFAULT TRUE,

    -- V9 se resuelve aparte de la ingesta (llamadas HTTP a BDNS): al insertar,
    -- v9_historial es provisional (=1) y v9_estado indica si falta resolverlo.
    -- resolverHistorial.py completa la fila (mismo id, no una nueva versión) y
    -- pasa v9_estado a 'calculado'. Ver migrations/007_historial_v9_desacoplado.sql.
    v9_estado        TEXT NOT NULL DEFAULT 'pendiente'
                         CHECK (v9_estado IN ('pendiente', 'calculado', 'sin_organo', 'error')),
    v9_intentos      SMALLINT NOT NULL DEFAULT 0,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Solo el scoring vigente por convocatoria (el más reciente)
CREATE INDEX IF NOT EXISTS idx_scoring_vigente
    ON scoring_results (convocatoria_id)
    WHERE es_vigente = TRUE;

-- Cola de resolverHistorial.py: vigentes con V9 aún sin resolver o a reintentar
CREATE INDEX IF NOT EXISTS idx_scoring_v9_pendiente
    ON scoring_results (created_at)
    WHERE es_vigente = TRUE AND v9_estado IN ('pendiente', 'error');

-- =============================================================================
-- 5b. historial_cache — Historial BDNS cacheado por organismo + título
--     normalizado (sin año), para no repetir la búsqueda entre ediciones
--     sucesivas del mismo programa ni entre convocatorias del mismo emisor.
-- =============================================================================
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

-- =============================================================================
-- 6. feedback — Valoraciones del equipo sobre scorings
-- =============================================================================
CREATE TABLE IF NOT EXISTS feedback (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scoring_id     UUID NOT NULL REFERENCES scoring_results(id),
    usuario        TEXT NOT NULL,
    valoracion     TEXT NOT NULL CHECK (valoracion IN
                       ('correcto', 'demasiado_alto', 'demasiado_bajo')),
    nivel_sugerido TEXT,
    nota           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 7. scoring_config — Versiones de pesos y umbrales
-- =============================================================================
CREATE TABLE IF NOT EXISTS scoring_config (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version       INTEGER NOT NULL,
    pesos         JSONB NOT NULL,
    umbrales      JSONB NOT NULL,
    activa        BOOLEAN NOT NULL DEFAULT FALSE,
    propuesta_por TEXT,
    aprobada_por  TEXT,
    aprobada_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Solo puede haber una configuración activa al mismo tiempo
CREATE UNIQUE INDEX IF NOT EXISTS idx_config_activa
    ON scoring_config (activa)
    WHERE activa = TRUE;

-- =============================================================================
-- 8. learning_runs — Historial de reentrenamientos del motor de aprendizaje
-- =============================================================================
CREATE TABLE IF NOT EXISTS learning_runs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo                 TEXT NOT NULL CHECK (tipo IN ('loop_a', 'loop_b', 'ambos')),
    n_muestras           INTEGER,
    pesos_anteriores     JSONB,
    pesos_sugeridos      JSONB,
    umbrales_anteriores  JSONB,
    umbrales_sugeridos   JSONB,
    estado               TEXT NOT NULL DEFAULT 'pendiente'
                             CHECK (estado IN ('pendiente', 'aprobado', 'rechazado')),
    metricas             JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 9. Trigger: actualización automática de updated_at en convocatorias
-- =============================================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_conv_updated_at ON convocatorias;
CREATE TRIGGER trg_conv_updated_at
    BEFORE UPDATE ON convocatorias
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =============================================================================
-- 10. Configuración inicial de scoring (versión v1)
--     Pesos y umbrales por defecto definidos en CLAUDE.md
-- =============================================================================
INSERT INTO scoring_config (version, pesos, umbrales, activa, propuesta_por)
VALUES (
    1,
    '{
        "v1_tramitacion_estandarizada": 1.0,
        "v2_volumen":                   1.2,
        "v3_cuantia":                   1.0,
        "v4_intermediarios":            1.4,
        "v5_presupuesto_recurrente":    1.1,
        "v6_cultura_gestion":           1.1,
        "v7_req_tecnicos":              1.3,
        "v8_plazo_cobro":               1.4,
        "v9_historial":                 1.3
    }',
    '{
        "azul":        80,
        "oportunidad": 65,
        "revisar":     50
    }',
    TRUE,
    'sistema'
)
ON CONFLICT DO NOTHING;

-- =============================================================================
-- Verificación final
-- =============================================================================
DO $$
DECLARE
    v_tablas  INTEGER;
    v_indices INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_tablas
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name IN (
          'organos', 'catalogo_items', 'convocatorias', 'documentos',
          'scoring_results', 'feedback', 'scoring_config', 'learning_runs',
          'historial_cache'
      );

    SELECT COUNT(*) INTO v_indices
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND tablename IN (
          'organos', 'catalogo_items', 'convocatorias', 'documentos',
          'scoring_results', 'scoring_config', 'historial_cache'
      );

    RAISE NOTICE '==============================================';
    RAISE NOTICE 'Esquema Océano Azul inicializado correctamente';
    RAISE NOTICE 'Tablas creadas : %/9', v_tablas;
    RAISE NOTICE 'Índices creados: %', v_indices;
    RAISE NOTICE '==============================================';
END;
$$;
