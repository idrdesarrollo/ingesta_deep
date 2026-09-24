# Océano Azul — Robot de convocatorias (contexto para Claude Code)

## Qué es
Robot de extracción diaria del BDNS (subvenciones públicas españolas).
Alcance de ESTE repositorio: **extracción + persistencia + scoring + embedding
a nivel convocatoria**. Nada más.

Pipeline: API BDNS → UPSERT PostgreSQL → scoring V1–V9 (reglas) → embedding BGE-M3.

## Fuera de alcance (a propósito)
No vive aquí, y no debe añadirse sin decidirlo antes:
- Troceado de PDF / `documento_chunks` (nivel fragmento).
- Recuperación híbrida, reranking, generación con LLM.
- Cualquier dependencia de PDF u OCR (pymupdf, pytesseract).

Ese es otro proyecto. Este robot debe seguir siendo ligero y rápido.

## Stack
- Python >= 3.10 (verificado con 3.13) en entorno virtual `.venv`
  (`requirements.txt`). SO: Windows. Sin Robocorp: cada tarea es un script
  `python <tarea>.py`; la programación horaria es externa (Programador de
  tareas de Windows).
- Secretos por variables de entorno, cargadas desde `.env` (ver `.env.example`).
- PostgreSQL con `vector` (pgvector) y `uuid-ossp`.
- psycopg2, pandas, sentence-transformers, python-dotenv.
- API BDNS: https://www.infosubvenciones.es/bdnstrans/api

## Mapa del código
- `convocatorias.py`, `convRecientes.py`, `convocatoriasFecha.py`,
  `updateConvoca.py`, `updateInfo.py`, `resolverHistorial.py` — las 6
  tareas (scripts con `if __name__ == '__main__'`).
- `keywords/db.py` — conexión PostgreSQL (psycopg2, autocommit, filas
  `DictRow` accesibles por índice y por nombre). Sustituye a `RPA.Database`
  con la misma semántica; carga `.env`.
- `keywords/listar_convocatorias.py` — cliente de la API del BDNS.
- `keywords/datBBDD.py` — UPSERT. `databaseInsert()` es el punto central:
  tras el UPSERT llama a `scorer.calcular_y_guardar()` y a
  `embedder.calcular_y_guardar()`. No romper ese orden.
- `keywords/scorer.py` — scoring V1–V9 (reglas + keywords + campos
  estructurados). 100% offline a propósito: no importa `requests`. V9 se
  guarda provisional (=1) en la ingesta; `historial.py`/`resolverHistorial.py`
  lo resuelven después. Ver "V9 desacoplado de la ingesta" más abajo.
- `keywords/historial.py` — la única parte del motor de scoring que depende
  de red (llamadas a BDNS para V9). Solo la llama `resolverHistorial.py`,
  nunca la ingesta.
- `keywords/embedder.py` — embedding BGE-M3 de descripción + bases reguladoras.
- `keywords/resolucion_fechas.py` — parseo de fechas en texto libre (es/ca/gl) y
  cálculo de `estadoVigencia`. Sin dependencias de BD: testeable
  en aislado. `datBBDD.py` solo llama a `resolver_fechas()`/`format_fecha()`.
- `resolverHistorial.py` — tarea aparte (su propio horario en el scheduler)
  que resuelve `scoring_results.v9_estado='pendiente'/'error'` por lotes,
  con caché (`historial_cache`) y throttling. Nunca se llama desde la
  ingesta principal.
- `variables/variables_.py` — catálogos.
- `scripts/backfill_embeddings.py` — backfill masivo. Se ejecuta FUERA del
  entorno del robot (necesita torch con CUDA).
- `scripts/backfill_id_bdns_nivel3.py` — backfill de `organos.id_bdns_nivel3`
  para órganos creados antes de la migración 008. Se ejecuta fuera del robot
  (psycopg2 directo). Idempotente (`WHERE id_bdns_nivel3 IS NULL`).

## V9 desacoplado de la ingesta (historial de recurrencia)
`buscar_historial_previo()` hace 1-3 llamadas HTTP a BDNS por convocatoria.
Antes vivía dentro de `scorer.calcular_y_guardar()`, en la ruta caliente de
`databaseInsert()` — con `pageSize=10000` en "Traer todas las convocatorias"
eso multiplicaba el tiempo de ingesta y arriesgaba que BDNS bloqueara la IP.

Dos IDs de órgano distintos, no confundir:
- `organos.id` — SERIAL propio, interno. Es el FK de `convocatorias.organo_id`.
- `organos.id_bdns_nivel3` — el ID numérico que BDNS usa internamente para
  ese mismo organismo en su propio catálogo (`/organos`). Sin relación
  aritmética con `organos.id`. Es el único que entiende el parámetro
  `?organos=X` de `/convocatorias/busqueda`, que es lo que necesita V9.

`id_bdns_nivel3` se resuelve por texto (comparando `nivel3` contra el
catálogo `df_organos` cacheado — `datBBDD.get_id_organo_bdns()`) **una sola
vez por órgano, dentro de `datBBDD.buscar_organo_id()`**, y se persiste. No
se recalcula por convocatoria ni se vuelve a resolver en
`resolverHistorial.py`: ese script solo hace un JOIN para leerlo. Repetir la
resolución por texto en un segundo sitio reintroduciría el mismo punto
frágil (mayúsculas/tildes/espacios distintos entre el endpoint de
convocatorias y el de organismos de BDNS pueden hacer fallar el match en
silencio) dos veces en vez de una.

Flujo completo:
1. La ingesta (`datBBDD.buscar_organo_id`) resuelve y guarda
   `organos.id_bdns_nivel3` la primera vez que ve un órgano (y reintenta si
   quedó NULL en un intento anterior).
2. `scorer.calcular_y_guardar()` calcula V1-V8 sin red, guarda V9=1
   (conservador) y `v9_estado='pendiente'` — siempre, sin mirar el órgano.
3. `resolverHistorial.py`, en su propio horario:
   a. Pasa a `v9_estado='sin_organo'`, sin tocar BDNS, las filas cuyo
      `organos.id_bdns_nivel3` sigue NULL (órgano sin resolver).
   b. Para el resto, resuelve el historial (con caché en `historial_cache`
      por `organo_id` + título normalizado sin año —
      `historial.normalizar_titulo()`— y throttling de 0.5s entre llamadas
      reales a BDNS) y completa la MISMA fila de `scoring_results` (no crea
      una versión nueva) vía `scorer.recalcular_con_historial()`, que
      reutiliza `pesos_snapshot` de la fila (no la config activa actual) y
      los v1-v8 ya guardados.

Trampa a no repetir: si se añade otro sitio que llame a
`historial.buscar_historial_previo()` o a `datBBDD.get_id_organo_bdns()`
directamente desde la ingesta o desde el resolver (en vez de leer
`organos.id_bdns_nivel3` ya persistido), se reintroduce el cuello de botella
y la fragilidad de matching que esto resolvió.

## Convenciones
- Migraciones en `migrations/NNN_descripcion.sql`: numeradas, con cabecera de
  comentario, `BEGIN;`…`COMMIT;` y un `DO $$` de verificación al final.
- `schema_init.sql` refleja el estado FINAL del esquema (ya incluye 003, 006,
  007 y 008). Si se añade una migración, actualizar también `schema_init.sql`.
- El texto está en español; el FTS usa la configuración `spanish`.
- Embeddings siempre normalizados (`normalize_embeddings=True`) y con
  `vector_cosine_ops`, para casar con el índice HNSW.
- `embedder._texto_para_embedder()` y `scripts/backfill_embeddings.py` deben
  construir el MISMO texto. Si cambia uno, cambia el otro o se mezclan
  embeddings de dos criterios distintos.

## Trampas conocidas (no repetir)
- La conexión va en autocommit a propósito (igual que `RPA.Database`): un
  error en scoring/embedding no debe deshacer el UPSERT ni dejar la conexión
  en estado abortado para las convocatorias siguientes.
- `_update_documentos` usa `ON CONFLICT (convocatoria_id, url)`: depende del
  índice único `idx_docs_conv_url` (migración 006). No volver al DELETE+INSERT:
  churnea los ids de documentos innecesariamente.
- `embedder` carga el modelo con singleton perezoso. No cargarlo por fila.

## Reglas para Claude Code
- Preguntar antes de modificar el esquema de la BD.
- Trabajar en rama de git; mostrar el diff y esperar aprobación.
- Nunca commitear credenciales ni el DSN: van en `.env` (ignorado por git) o por variable de entorno.
- Antes de una migración o un backfill: recordar `pg_dump`.
