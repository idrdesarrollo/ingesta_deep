"""
Backfill de embeddings BGE-M3 (1024 dim) para la tabla `convocatorias`.

Recorre las convocatorias sin embedding, calcula el vector con BGE-M3 por
lotes y lo guarda en la columna `embedding`. Es idempotente y reanudable:
solo procesa filas con `embedding IS NULL`, así que si se corta lo relanzas
y sigue donde iba.

Requisitos: los de requirements.txt, pero con torch CUDA (ver README).
    GPU (RTX 4060): comprueba que torch ve CUDA -> python -c "import torch; print(torch.cuda.is_available())"

Antes de correr:
    1. Aplica la migración 003 (embedding -> vector(1024)).
    2. Configura la conexión (ver DSN abajo). Prueba primero contra una BD de test.

Después de correr:
    Recrea el índice HNSW (con la tabla ya poblada):
        CREATE INDEX idx_conv_embedding
            ON convocatorias USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
"""

import os
import sys

import psycopg2
from psycopg2.extras import execute_batch
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

# --- Configuración -----------------------------------------------------------
# Conexión por variable de entorno OCEANO_DSN (o en .env), para no dejar
# credenciales en el código. Ver .env.example.
DSN        = os.environ.get("OCEANO_DSN", "dbname=oceano_azul user=postgres host=localhost port=5432")
MODEL_NAME = os.environ.get("BGE_MODEL", "BAAI/bge-m3")
BATCH      = int(os.environ.get("BACKFILL_BATCH", "128"))   # filas por lote; baja el número si te falta VRAM


def texto_para_embedder(descripcion: str, bases: str) -> str:
    """Texto que representa a la convocatoria. Por ahora: descripción + bases.
    Aquí es donde afinar QUÉ se embebe (p.ej. añadir finalidad o sectores)."""
    partes = [descripcion or "", bases or ""]
    return "\n".join(p.strip() for p in partes if p and p.strip())


def main() -> None:
    print(f"Cargando modelo {MODEL_NAME} (la 1ª vez descarga ~2,2 GB)...")
    model = SentenceTransformer(MODEL_NAME)   # se carga UNA sola vez, fuera del bucle

    conn = psycopg2.connect(DSN)
    register_vector(conn)                     # permite pasar el np.array directamente como vector
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM convocatorias WHERE embedding IS NULL")
    pendientes = cur.fetchone()[0]
    print(f"Convocatorias sin embedding: {pendientes}")
    if pendientes == 0:
        print("Nada que hacer.")
        cur.close()
        conn.close()
        return

    procesadas = 0
    while True:
        cur.execute(
            """
            SELECT id, descripcion, bases_reguladoras
            FROM convocatorias
            WHERE embedding IS NULL
            LIMIT %s
            """,
            (BATCH,),
        )
        filas = cur.fetchall()
        if not filas:
            break

        ids    = [f[0] for f in filas]
        textos = [texto_para_embedder(f[1], f[2]) for f in filas]

        # Un solo pase por GPU para todo el lote. normalize_embeddings=True hace
        # que coseno = producto punto, que es lo que casa con vector_cosine_ops.
        vecs = model.encode(textos, normalize_embeddings=True, show_progress_bar=False)

        execute_batch(
            cur,
            "UPDATE convocatorias SET embedding = %s WHERE id = %s",
            [(vec, conv_id) for conv_id, vec in zip(ids, vecs)],
        )
        conn.commit()                          # progreso guardado por lote: si se corta, no repites todo

        procesadas += len(filas)
        print(f"  {procesadas}/{pendientes} convocatorias embebidas...", flush=True)

    cur.close()
    conn.close()
    print("Backfill completado. Recuerda recrear el índice HNSW (ver cabecera del archivo).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrumpido. Relanza el script para continuar donde iba.")
        sys.exit(1)
