"""
Backfill de organos.id_bdns_nivel3 para los órganos creados ANTES de la
migración 008.

Sin esto, resolverHistorial.py marcaría como 'sin_organo' (V9 se queda en 1
para siempre) a todas las convocatorias de órganos ya existentes, aunque el
órgano sí tenga un ID BDNS resoluble -simplemente nunca se calculó ni
persistió porque esa columna no existía todavía.

Es idempotente y reanudable: solo toca filas con id_bdns_nivel3 IS NULL, así
que si se corta lo relanzas y sigue donde iba. La resolución en sí es la
misma de siempre (comparación de texto contra el catálogo /organos de BDNS,
ver keywords/get_organo.py y keywords/datBBDD.get_id_organo_bdns) -aquí solo
se hace una vez más, a mano, para las filas antiguas.

Antes de correr:
    1. Aplica la migración 008 (organos.id_bdns_nivel3).
    2. Configura la conexión (ver DSN abajo).

Uso:
    python scripts/backfill_id_bdns_nivel3.py
"""

import os
import sys

import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from keywords import get_organo  # noqa: E402  (requiere el sys.path.insert de arriba)

load_dotenv()
DSN = os.environ.get(
    "OCEANO_DSN",
    "dbname=oceano_azul user=postgres host=localhost port=5432",
)


def main() -> None:
    print("Descargando catálogo de órganos de BDNS (4 peticiones a /organos)...")
    df_organos = get_organo.main()
    print(f"Catálogo cargado: {len(df_organos)} órganos.")

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    cur.execute("SELECT id, nivel3 FROM organos WHERE id_bdns_nivel3 IS NULL")
    filas = cur.fetchall()
    print(f"Órganos sin id_bdns_nivel3: {len(filas)}")
    if not filas:
        print("Nada que hacer.")
        cur.close()
        conn.close()
        return

    actualizaciones = []
    sin_match = 0
    for organo_id, nivel3 in filas:
        if not nivel3:
            sin_match += 1
            continue
        coincidencia = df_organos[df_organos['organo'] == nivel3]
        if len(coincidencia) == 0:
            sin_match += 1
            continue
        try:
            id_bdns = int(coincidencia['ID'].values[0])
        except (ValueError, TypeError):
            sin_match += 1
            continue
        actualizaciones.append((id_bdns, organo_id))

    execute_batch(
        cur,
        "UPDATE organos SET id_bdns_nivel3 = %s WHERE id = %s",
        actualizaciones,
    )
    conn.commit()

    print(f"Resueltos: {len(actualizaciones)}/{len(filas)}")
    print(f"Sin coincidencia en el catálogo (quedan v9_estado='sin_organo' hasta "
          f"que se puedan resolver a mano o BDNS actualice su catálogo): {sin_match}")

    cur.close()
    conn.close()
    print("Backfill completado.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrumpido. Relanza el script para continuar donde iba.")
        sys.exit(1)
