# Océano Azul — Robot de convocatorias (BDNS)

Robot de extracción diaria del **Sistema Nacional de Publicidad de Subvenciones
y Ayudas Públicas (BDNS)**. Descarga las convocatorias desde la API pública, las
guarda en PostgreSQL, las puntúa con el scoring «Océano Azul» (criterios V1–V9,
por reglas, sin LLM) y calcula su **embedding semántico BGE-M3**, que es la base
de la búsqueda semántica / RAG que consumen otros proyectos.

> Documentación técnica detallada (diagramas, modelo de datos, decisiones de
> diseño): [`ARQUITECTURA.md`](ARQUITECTURA.md).

---

## Índice

1. [Alcance](#alcance)
2. [Pipeline](#pipeline)
3. [Requisitos y librerías](#requisitos-y-librerías)
4. [Instalación](#instalación)
5. [Configuración (.env)](#configuración-env)
6. [Base de datos](#base-de-datos)
7. [Ejecución de las tareas](#ejecución-de-las-tareas)
8. [Programación diaria](#programación-diaria)
9. [Motor de scoring V1–V9](#motor-de-scoring-v1v9)
10. [V9: historial desacoplado de la ingesta](#v9-historial-desacoplado-de-la-ingesta)
11. [Embeddings (BGE-M3)](#embeddings-bge-m3)
12. [Búsqueda semántica y RAG](#búsqueda-semántica-y-rag)
13. [Estructura del proyecto](#estructura-del-proyecto)
14. [Buenas prácticas y trampas conocidas](#buenas-prácticas-y-trampas-conocidas)

---

## Alcance

Este repositorio trabaja **a nivel convocatoria** y debe seguir siendo ligero y
rápido.

| Incluido | Fuera de alcance (otro proyecto) |
|---|---|
| Ingesta desde la API BDNS | Troceado de PDF / `documento_chunks` |
| UPSERT en PostgreSQL | Recuperación híbrida y reranking |
| Scoring V1–V9 por reglas (offline) | Generación de respuestas con LLM |
| Embedding BGE-M3 de cada convocatoria | PDF / OCR (pymupdf, pytesseract) |
| Resolución diferida del historial V9 | API HTTP y frontend |

---

## Pipeline

```
                     ┌──────────────────── keywords/datBBDD.py::databaseInsert() ───────────────────┐
API BDNS  ──────►    │  UPSERT convocatorias  ──►  scoring V1–V8  ──►  embedding BGE-M3              │
(listar_convocatorias)│  (+ órgano, catálogos,     (V9 = 1 provisional,  (descripción + bases        │
                     │   documentos, fechas)       v9_estado=pendiente)   reguladoras → VECTOR(1024)) │
                     └──────────────────────────────────────────────────────────────────────────────┘

resolverHistorial.py (horario propio)  ──►  V9 real contra BDNS (por lotes, caché, throttling)
                                             └─► completa la misma fila de scoring_results
```

El orden **UPSERT → scoring → embedding** dentro de `databaseInsert()` es
obligatorio: el scoring necesita la fila insertada y el embedding actualiza esa
misma fila. Un error en scoring o embedding se registra pero **no** interrumpe
la ingesta del resto de convocatorias.

---

## Requisitos y librerías

- **Python ≥ 3.10** (verificado con 3.13), en entorno virtual `.venv`.
- **Windows** (la programación se hace con el Programador de tareas).
- **PostgreSQL 15+** con las extensiones `vector` (pgvector) y `uuid-ossp`.
- Acceso a Internet: API BDNS y, la primera vez, Hugging Face (descarga del
  modelo BGE-M3, ~2,2 GB).

Dependencias (`requirements.txt`, versiones fijadas):

| Librería | Uso |
|---|---|
| `requests` | Cliente de la API BDNS |
| `psycopg2-binary` | Conexión PostgreSQL (`keywords/db.py`) |
| `pgvector` | Adaptador de vectores (solo `scripts/backfill_embeddings.py`) |
| `python-dotenv` | Carga de credenciales desde `.env` |
| `pandas` | Catálogo de órganos BDNS y utilidades de datos |
| `sentence-transformers` | Carga y ejecución del modelo BGE-M3 |
| `transformers`, `tokenizers`, `huggingface_hub`, `safetensors` | Dependencias del modelo |
| `torch` | Inferencia (build CPU para el robot; CUDA para el backfill) |
| `numpy` | Vectores |

> Las versiones de ML (torch, transformers, sentence-transformers…) están
> probadas juntas. Si se suben, comprobar antes que el modelo carga:
> `python -c "from keywords import embedder; embedder._get_model()"`.

---

## Instalación

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env      # y rellenar credenciales
```

Para trabajar con el entorno activado: `.venv\Scripts\Activate.ps1`.

---

## Configuración (.env)

Todas las credenciales van por variables de entorno, cargadas desde `.env`
(ignorado por git). Plantilla: `.env.example`.

| Variable | Uso | Por defecto |
|---|---|---|
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Conexión del robot (`keywords/db.py`) | `oceano_azul`, `postgres`, —, `localhost`, `5432` |
| `OCEANO_DSN` | DSN completo para los scripts de `scripts/` | — |
| `BGE_MODEL` | Modelo de embeddings | `BAAI/bge-m3` |
| `HF_TOKEN` | Token de Hugging Face (opcional) | — |
| `BACKFILL_BATCH` | Filas por lote en el backfill de embeddings | `128` |

**Nunca** commitear `.env`, credenciales ni el DSN.

---

## Base de datos
Tablas principales:

| Tabla | Contenido |
|---|---|
| `convocatorias` | Convocatoria BDNS; incluye `score_pct`, `nivel_scoring` (desnormalizados) y `embedding VECTOR(1024)` |
| `organos` | Órganos convocantes; `id` interno y `id_bdns_nivel3` (ID de BDNS, para V9) |
| `documentos` | Documentos adjuntos de cada convocatoria (UPSERT por `convocatoria_id, url`) |
| `scoring_results` | V1–V9, `score_raw`, `score_pct`, nivel, `pesos_snapshot`, `v9_estado` |
| `historial_cache` | Caché de historial V9 por órgano + título normalizado (TTL 30 días) |
| `scoring_config` | Pesos y umbrales activos (este repo solo los **lee**) |
| `feedback`, `learning_runs` | Aprendizaje supervisado (sin productor en este repo) |

Convención de migraciones: `migrations/NNN_descripcion.sql`, con cabecera,
`BEGIN;`…`COMMIT;` y un bloque `DO $$` de verificación. Si se añade una,
actualizar también `schema_init.sql`.

---

## Ejecución de las tareas

Cada tarea es un script independiente; se lanza desde la raíz del proyecto:

| Tarea | Comando | Para qué |
|---|---|---|
| Traer todas las convocatorias | `python convocatorias.py` | Carga histórica completa |
| Traer convocatorias por fecha | `python convocatoriasFecha.py --desde 01/09/2026 --hasta 15/09/2026` | Rango concreto (`dd/mm/aaaa`) |
| Traer convocatorias recientes | `python convRecientes.py` | **Ejecución diaria** (D-1; solo inserta las nuevas) |
| Actualizar convocatorias | `python updateConvoca.py` | Cierra las abiertas con plazo vencido (sin red) |
| Actualizar información | `python updateInfo.py` | Refresca abiertas / pendientes / indefinidas contra BDNS |
| Resolver historial V9 | `python resolverHistorial.py` | Calcula V9 por lotes (horario propio) |

(`python` = `.venv\Scripts\python.exe`, o activar antes el entorno.)

Cada tarea abre y cierra su propia conexión. Todas son **idempotentes**: se
pueden relanzar sin duplicar datos.

---

## Programación diaria

Con el Programador de tareas de Windows (ejemplo: ingesta a las 06:00, V9 a las
07:30, refresco a las 08:30):

```powershell
$dir = "C:\ruta\a\obtencion_convocatorias_python"
mkdir "$dir\logs" -Force
schtasks /Create /TN "OceanoAzul\convRecientes"      /SC DAILY /ST 06:00 /TR "cmd /c cd /d $dir && .venv\Scripts\python.exe convRecientes.py >> logs\convRecientes.log 2>&1"
schtasks /Create /TN "OceanoAzul\resolverHistorial"  /SC DAILY /ST 07:30 /TR "cmd /c cd /d $dir && .venv\Scripts\python.exe resolverHistorial.py >> logs\resolverHistorial.log 2>&1"
schtasks /Create /TN "OceanoAzul\updateConvoca"      /SC DAILY /ST 08:30 /TR "cmd /c cd /d $dir && .venv\Scripts\python.exe updateConvoca.py >> logs\updateConvoca.log 2>&1"
```

---

## Motor de scoring V1–V9

`keywords/scorer.py`. Nueve criterios de 1 a 5, cada uno con un peso. Es
**100 % offline** (no hace llamadas de red): reglas por capas → keywords de alta
señal, campos estructurados de BDNS, instrumento como proxy y, por último, un
valor conservador. Keywords validadas contra 23.834 registros reales de BDNS.

| Criterio | Qué mide | Peso |
|---|---|---|
| V1 `tramitacion_estandarizada` | Cuántos expedientes siguen el mismo flujo | 1.0 |
| V2 `volumen` | Beneficiarios potenciales | 1.2 |
| V3 `cuantia` | Presupuesto total | 1.0 |
| V4 `intermediarios` | Fortaleza del canal intermediario | 1.4 |
| V5 `presupuesto_recurrente` | Longevidad del programa | 1.1 |
| V6 `cultura_gestion` | Cultura de gestión de subvenciones del sector | 1.1 |
| V7 `req_tecnicos` | Requisitos técnicos y certificaciones | 1.3 |
| V8 `plazo_cobro` | Plazo de cobro estimado | 1.4 |
| V9 `historial` | Recurrencia histórica del programa (diferido) | 1.3 |

```
score_raw = Σ (criterio_k × peso_k)          máximo = Σ (5 × peso_k) = 54.0
score_pct = score_raw / máximo × 100
```

| Nivel | Umbral | Color |
|---|---|---|
| 🟢 `azul` | ≥ 80 % | `#1D9E75` |
| 🔵 `oportunidad` | 65–80 % | `#378ADD` |
| 🟡 `revisar` | 50–65 % | `#EF9F27` |
| 🔴 `descartar` | < 50 % | `#E24B4A` |

Pesos y umbrales se leen de `scoring_config WHERE activa = TRUE`; si no hay
configuración activa se usan `PESOS_DEFAULT` / `UMBRALES_DEFAULT`. Cada cálculo
guarda los pesos usados en `pesos_snapshot`, de modo que cambiar la
configuración no reinterpreta cálculos pasados.

---

## V9: historial desacoplado de la ingesta

Calcular V9 requiere 1–3 llamadas a BDNS por convocatoria. Para no ralentizar la
ingesta ni arriesgar un bloqueo de IP:

1. **Ingesta:** `datBBDD.buscar_organo_id()` resuelve por texto y guarda
   `organos.id_bdns_nivel3` **una sola vez por órgano**.
2. **Scoring:** V1–V8 se calculan sin red; V9 se guarda provisional (=1) con
   `v9_estado = 'pendiente'`.
3. **`resolverHistorial.py`** (lotes de 200, 0,5 s entre llamadas, máx. 5
   intentos):
   - marca `sin_organo` las filas cuyo órgano no tiene `id_bdns_nivel3`;
   - para el resto consulta `historial_cache` (órgano + título normalizado,
     TTL 30 días) o, si no hay caché, llama a BDNS (`keywords/historial.py`);
   - completa **la misma fila** de `scoring_results` con
     `scorer.recalcular_con_historial()`, reutilizando su `pesos_snapshot`.

Estados de `v9_estado`: `pendiente` → `calculado` | `sin_organo` | `error`
(se reintenta hasta 5 veces).

> ⚠️ No llamar a `historial.buscar_historial_previo()` ni a
> `datBBDD.get_id_organo_bdns()` desde la ingesta ni desde el resolver: leer
> siempre `organos.id_bdns_nivel3` ya persistido.

---

## Embeddings (BGE-M3)

| Aspecto | Valor |
|---|---|
| Modelo | [`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) (multilingüe, bueno en español/catalán/gallego) |
| Dimensión | 1024 (`convocatorias.embedding VECTOR(1024)`) |
| Normalización | `normalize_embeddings=True` → coseno = producto punto |
| Texto embebido | `descripcion` + `\n` + bases reguladoras |
| Índice | HNSW, `vector_cosine_ops`, `m = 16`, `ef_construction = 64` |
| Librería | `sentence-transformers` |

**Dos caminos, mismo texto:**

- **En la ingesta** — `keywords/embedder.py::calcular_y_guardar()`: una
  convocatoria cada vez, torch CPU. El modelo se carga una sola vez por proceso
  (singleton perezoso).
- **Backfill masivo** — `scripts/backfill_embeddings.py`: para convocatorias ya
  cargadas sin vector (`WHERE embedding IS NULL`), por lotes y con GPU.
  Reanudable. Se ejecuta **fuera** del entorno del robot, en un venv aparte
  para no cambiar el torch CPU:

```powershell
py -3.13 -m venv .venv-gpu
.venv-gpu\Scripts\python.exe -m pip install -r requirements.txt
.venv-gpu\Scripts\python.exe -m pip install --force-reinstall torch==2.13.0 --index-url https://download.pytorch.org/whl/cu128   # ajustar cuXXX a tu CUDA
.venv-gpu\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
.venv-gpu\Scripts\python.exe scripts/backfill_embeddings.py   # lee OCEANO_DSN de .env
```

Después del backfill, (re)crear el índice HNSW con la tabla ya poblada:

```sql
CREATE INDEX IF NOT EXISTS idx_conv_embedding
    ON convocatorias USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

> ⚠️ `embedder._texto_para_embedder()` y `texto_para_embedder()` del backfill
> deben construir **exactamente el mismo texto**. Si se cambia uno, cambiar el
> otro y regenerar los embeddings; si no, se mezclan vectores de dos criterios y
> la búsqueda degrada sin ningún error visible.

---

## Búsqueda semántica y RAG

Este robot deja preparada la **capa de recuperación a nivel convocatoria**:
vectores BGE-M3 normalizados con índice HNSW y un índice de texto completo en
español (`to_tsvector('spanish', descripcion)`). La recuperación híbrida, el
reranking, el troceado de documentos y la generación con LLM viven en otro
proyecto, que consume estas tablas.

Para consultar, la pregunta se codifica **con el mismo modelo y normalizada**:

```python
from sentence_transformers import SentenceTransformer
from keywords.db import conectar

model = SentenceTransformer("BAAI/bge-m3")
q = model.encode("ayudas para digitalización de pymes", normalize_embeddings=True)
q_pg = "[" + ",".join(f"{x:.8f}" for x in q) + "]"

db = conectar()
filas = db.query(
    """
    SELECT id, descripcion, score_pct, nivel_scoring,
           1 - (embedding <=> %s::vector) AS similitud
    FROM convocatorias
    WHERE embedding IS NOT NULL AND estado_vigencia = 'Abierta'
    ORDER BY embedding <=> %s::vector
    LIMIT 10
    """,
    data=(q_pg, q_pg),
)
```

- `<=>` es la distancia coseno de pgvector (usa el índice HNSW).
- Se puede combinar con filtros (vigencia, nivel, región…) y con el score
  Océano Azul para priorizar resultados.
- Búsqueda léxica equivalente:
  `WHERE to_tsvector('spanish', descripcion) @@ plainto_tsquery('spanish', '...')`.

---

## Estructura del proyecto

```
convocatorias.py            Tarea: traer todas las convocatorias
convRecientes.py            Tarea: traer recientes (ejecución diaria)
convocatoriasFecha.py       Tarea: traer por rango de fechas (--desde / --hasta)
updateConvoca.py            Tarea: cerrar convocatorias vencidas
updateInfo.py               Tarea: refrescar abiertas contra BDNS
resolverHistorial.py        Tarea: resolver V9 por lotes
keywords/
  db.py                     Conexión PostgreSQL (psycopg2, autocommit, filas DictRow) + .env
  listar_convocatorias.py   Cliente de la API BDNS (paginación)
  datBBDD.py                UPSERT; orquesta scoring + embedding (databaseInsert)
  get_organo.py             Catálogo de órganos BDNS (DataFrame cacheado)
  resolucion_fechas.py      Parseo de fechas en texto libre (es/ca/gl) y estado de vigencia
  scorer.py                 Scoring V1–V9 (reglas + keywords, offline)
  historial.py              V9 contra BDNS (solo lo usa resolverHistorial.py)
  embedder.py               Embedding BGE-M3
  format_str.py             Utilidades de texto
variables/variables_.py     Catálogos (reglamentos, finalidades, instrumentos…)
scripts/
  backfill_embeddings.py    Backfill masivo de embeddings (GPU, fuera del robot)
  backfill_id_bdns_nivel3.py Backfill de organos.id_bdns_nivel3 (idempotente)
migrations/                 Cambios de esquema numerados y transaccionales
schema_init.sql             Esquema completo (estado final)
requirements.txt            Dependencias
.env.example                Plantilla de configuración
ARQUITECTURA.md             Documento de arquitectura
```

API usada: `https://www.infosubvenciones.es/bdnstrans/api`
(`/convocatorias`, `/convocatorias/busqueda`, `/organos`).

---

## Buenas prácticas y trampas conocidas

- **Autocommit a propósito** (`keywords/db.py`): un fallo de scoring o embedding
  no deshace el UPSERT ni deja la conexión abortada.
- **Documentos:** `_update_documentos` usa `ON CONFLICT (convocatoria_id, url)`
  (índice único de la migración 006). No volver a DELETE + INSERT.
- **El modelo de embeddings no se carga por fila** (singleton).
- **`convRecientes.py` no actualiza** convocatorias existentes; eso lo hace
  `updateInfo.py`.
- Antes de modificar el esquema: preguntar, crear migración y actualizar
  `schema_init.sql`. Antes de una migración o backfill: **`pg_dump`**.
- Trabajar en rama de git; nunca commitear credenciales.
