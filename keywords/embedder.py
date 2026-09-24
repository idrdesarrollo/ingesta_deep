"""
Embeddings semánticos BGE-M3 (1024 dim) para la búsqueda RAG — sin LLM en el
camino de ingesta.

Calcula el embedding de cada convocatoria en el momento del alta/actualización
y lo persiste en convocatorias.embedding, para que las convocatorias nuevas no
dependan del backfill masivo (scripts/backfill_embeddings.py).

Punto de entrada externo: calcular_y_guardar(dat, db)
    Llamar desde datBBDD.databaseInsert() tras el UPSERT principal.
"""

import os
import traceback
from keywords.db import Database

_MODEL_NAME = os.environ.get("BGE_MODEL", "BAAI/bge-m3")
_model = None  # singleton perezoso: se carga una única vez por proceso


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        print(f'[embedder] Cargando modelo {_MODEL_NAME}...')
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def _texto_para_embedder(descripcion: str, bases: str) -> str:
    """Texto que representa a la convocatoria. Debe coincidir con el usado en
    scripts/backfill_embeddings.py para no mezclar embeddings de dos criterios distintos."""
    partes = [descripcion or "", bases or ""]
    return "\n".join(p.strip() for p in partes if p and p.strip())


def _to_pg_vector(vec) -> str:
    """Formatea un vector como literal pgvector: '[0.123,0.456,...]'."""
    return '[' + ','.join(f'{x:.8f}' for x in vec) + ']'


def calcular_y_guardar(dat: dict, db: Database) -> None:
    """
    Punto de entrada principal.
    1. Construye el texto de la convocatoria (descripción + bases reguladoras).
    2. Calcula el embedding BGE-M3 normalizado (coseno = producto punto).
    3. Actualiza convocatorias.embedding.
    Ante cualquier error interno no interrumpe el flujo de ingesta.
    """
    try:
        conv_id = dat['id']
        texto = _texto_para_embedder(
            dat.get('descripcion'),
            dat.get('descripcionBasesReguladoras'),
        )
        if not texto:
            return

        modelo = _get_model()
        vec = modelo.encode(texto, normalize_embeddings=True)

        db.query(
            "UPDATE convocatorias SET embedding = %s::vector WHERE id = %s",
            data=(_to_pg_vector(vec), conv_id)
        )

    except Exception as e:
        print(f'[embedder] Error calculando embedding para conv {dat.get("id")}: {e}')
        traceback.print_exc()
