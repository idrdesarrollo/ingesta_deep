"""Tarea: resuelve V9 (historial de recurrencia) por lotes.

Proceso aparte de la ingesta, con su propio horario (ver README). Toma
convocatorias cuyo scoring_results.v9_estado sigue en 'pendiente' o 'error'
(con reintentos disponibles), resuelve el historial contra BDNS -usando
historial_cache siempre que hay una entrada reciente del mismo organismo +
programa- y completa la fila con el score final.

La ingesta (datBBDD.databaseInsert -> scorer.calcular_y_guardar) nunca llama
a BDNS para esto: por eso este script existe.

Importante: el ID numérico que BDNS usa para el órgano (id_bdns_nivel3) NO
se recalcula aquí. Se resuelve una única vez, por texto, dentro de
datBBDD.buscar_organo_id() durante la ingesta, y se persiste en
organos.id_bdns_nivel3. Este script solo hace un JOIN para leerlo -así se
evita repetir esa resolución frágil (mayúsculas/tildes/espacios distintos
entre el endpoint de convocatorias y el de organismos de BDNS) en un
segundo sitio, y evita las 4 llamadas a /organos que le costaría reconstruir
el catálogo aquí también.
"""
import json
import time
from datetime import datetime, timedelta, timezone

from keywords import datBBDD
from keywords import scorer
from keywords import historial as historial_mod

LOTE = 200                 # convocatorias resueltas por corrida
TOPE_INTENTOS = 5          # tras esto, v9_estado='error' deja de reintentarse
THROTTLE_SEGUNDOS = 0.5    # pausa entre llamadas reales a BDNS (no en cache hits)
TTL_CACHE = timedelta(days=30)

_COLUMNAS_CRITERIOS = [
    'v1_tramitacion_estandarizada', 'v2_volumen', 'v3_cuantia',
    'v4_intermediarios', 'v5_presupuesto_recurrente', 'v6_cultura_gestion',
    'v7_req_tecnicos', 'v8_plazo_cobro',
]


def resolverHistorial():
    db = datBBDD.conect_database()
    try:
        _marcar_sin_organo(db)
        pendientes = _seleccionar_pendientes(db)
        for fila in pendientes:
            _resolver_fila(db, fila)
    finally:
        db.close()


def _marcar_sin_organo(db):
    """Pasa a 'sin_organo', sin gastar ni una llamada a BDNS, las filas cuyo
    órgano no tiene id_bdns_nivel3 resoluble (o no tiene órgano en absoluto).
    Así la selección de pendientes de abajo solo trae filas que sí se pueden
    resolver contra BDNS."""
    db.query(
        '''UPDATE scoring_results sr
           SET v9_estado = 'sin_organo'
           FROM convocatorias c
           LEFT JOIN organos o ON o.id = c.organo_id
           WHERE sr.convocatoria_id = c.id
             AND sr.es_vigente = TRUE
             AND sr.v9_estado = 'pendiente'
             AND o.id_bdns_nivel3 IS NULL'''
    )


def _seleccionar_pendientes(db):
    # keywords.db devuelve filas DictRow: se accede por nombre de columna
    # (fila['id']) sin reconstruir el dict a mano.
    return list(db.query(
        f'''SELECT sr.id, sr.convocatoria_id, sr.pesos_snapshot, sr.v9_intentos,
                   {', '.join('sr.' + c for c in _COLUMNAS_CRITERIOS)},
                   c.organo_id, c.descripcion, o.id_bdns_nivel3
            FROM scoring_results sr
            JOIN convocatorias c ON c.id = sr.convocatoria_id
            JOIN organos o ON o.id = c.organo_id
            WHERE sr.es_vigente = TRUE
              AND sr.v9_estado IN ('pendiente', 'error')
              AND sr.v9_intentos < %s
              AND o.id_bdns_nivel3 IS NOT NULL
            ORDER BY sr.created_at
            LIMIT %s''',
        data=(TOPE_INTENTOS, LOTE)
    ))


def _resolver_fila(db, fila):
    scoring_id = fila['id']
    organo_id = fila['organo_id']
    id_organo_nivel3 = fila['id_bdns_nivel3']
    titulo_original = fila['descripcion']
    titulo_normalizado = historial_mod.normalizar_titulo(titulo_original)

    try:
        historial = _obtener_historial(db, organo_id, id_organo_nivel3,
                                        titulo_original, titulo_normalizado)
    except historial_mod.HistorialBDNSError as e:
        _marcar_error(db, scoring_id, fila['v9_intentos'], e)
        return

    pesos = fila['pesos_snapshot'] if isinstance(fila['pesos_snapshot'], dict) \
        else json.loads(fila['pesos_snapshot'])
    _, umbrales = scorer.cargar_config_activa(db)
    criterios = {c: fila[c] for c in _COLUMNAS_CRITERIOS}

    resultado = scorer.recalcular_con_historial(criterios, historial, pesos, umbrales)
    nuevos = resultado['criterios']

    db.query(
        '''UPDATE scoring_results SET
               v9_historial   = %s,
               score_raw      = %s,
               score_pct      = %s,
               nivel_asignado = %s,
               v9_estado      = 'calculado'
           WHERE id = %s''',
        data=(nuevos['v9_historial'], resultado['score_raw'], resultado['score_pct'],
              resultado['nivel'], scoring_id)
    )
    db.query(
        "UPDATE convocatorias SET score_pct = %s, nivel_scoring = %s WHERE id = %s",
        data=(resultado['score_pct'], resultado['nivel'], fila['convocatoria_id'])
    )


def _obtener_historial(db, organo_id: int, id_organo_nivel3: int,
                        titulo_original: str, titulo_normalizado: str) -> dict:
    """titulo_normalizado es la clave de historial_cache (agrupa ediciones del
    mismo programa entre años); titulo_original es lo que se manda a la
    búsqueda de texto libre de la propia API de BDNS, sin tocar."""
    cacheado = _leer_cache(db, organo_id, titulo_normalizado)
    if cacheado is not None:
        return cacheado

    time.sleep(THROTTLE_SEGUNDOS)
    resultado = historial_mod.buscar_historial_previo(id_organo_nivel3, titulo_original)
    _guardar_cache(db, organo_id, titulo_normalizado, resultado)
    return resultado


def _leer_cache(db, organo_id: int, titulo_normalizado: str):
    filas = db.query(
        '''SELECT n_similares, anios_publicacion, tasa_ejecucion, calculado_at
           FROM historial_cache
           WHERE organo_id = %s AND titulo_normalizado = %s''',
        data=(organo_id, titulo_normalizado)
    )
    if not filas:
        return None

    n_similares, anios, tasa, calculado_at = filas[0]
    if datetime.now(timezone.utc) - _con_tz(calculado_at) > TTL_CACHE:
        return None

    return {
        'n_similares': n_similares,
        'anios_publicacion': [str(a) for a in (anios or [])],
        'tasa_ejecucion': float(tasa) if tasa is not None else None,
    }


def _guardar_cache(db, organo_id: int, titulo_normalizado: str, resultado: dict):
    anios_int = [int(a) for a in resultado.get('anios_publicacion', [])]
    anios_literal = '{' + ','.join(str(a) for a in anios_int) + '}'
    db.query(
        '''INSERT INTO historial_cache
               (organo_id, titulo_normalizado, n_similares, anios_publicacion,
                tasa_ejecucion, calculado_at)
           VALUES (%s, %s, %s, CAST(%s AS integer[]), %s, NOW())
           ON CONFLICT (organo_id, titulo_normalizado) DO UPDATE SET
               n_similares       = EXCLUDED.n_similares,
               anios_publicacion = EXCLUDED.anios_publicacion,
               tasa_ejecucion    = EXCLUDED.tasa_ejecucion,
               calculado_at      = NOW()''',
        data=(organo_id, titulo_normalizado, resultado['n_similares'],
              anios_literal, resultado['tasa_ejecucion'])
    )


def _marcar_error(db, scoring_id, intentos_previos: int, error: Exception):
    print(f'[resolverHistorial] scoring_id={scoring_id} intento {intentos_previos + 1}: {error}')
    db.query(
        "UPDATE scoring_results SET v9_estado = 'error', v9_intentos = v9_intentos + 1 WHERE id = %s",
        data=(scoring_id,)
    )


def _con_tz(dt: datetime) -> datetime:
    """Por si llega un datetime naive; asumimos UTC (calculado_at de Postgres
    es TIMESTAMPTZ, así que el valor ya está en ese instante)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


if __name__ == '__main__':
    resolverHistorial()
