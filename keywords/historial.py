"""Resolución de historial de recurrencia (V9) contra la API de BDNS.

Aislado de scorer.py a propósito: es la única parte del motor de scoring que
depende de red. Se llama SOLO desde resolverHistorial.py (proceso aparte,
por lotes, con throttling), nunca desde el flujo de ingesta principal
(datBBDD.databaseInsert -> scorer.calcular_y_guardar), para que la velocidad
de la ingesta no dependa de la latencia de BDNS.
"""
import re
import unicodedata
import requests

_BDNS_URL = 'https://www.infosubvenciones.es/bdnstrans/api'

_RE_ANIO = re.compile(r'\b(19|20)\d{2}\b')
_RE_NO_ALFANUM = re.compile(r'[^a-z0-9\s]')
_RE_ESPACIOS = re.compile(r'\s+')


class HistorialBDNSError(Exception):
    """Fallo al consultar BDNS (red, timeout, status != 200).

    Se distingue a propósito de un resultado vacío genuino (n_similares=0),
    que es una respuesta válida y no debe reintentarse indefinidamente.
    """


def normalizar_titulo(titulo: str) -> str:
    """minúsculas, sin tildes/puntuación, sin año -> clave estable para que
    ediciones sucesivas del mismo programa ("Convocatoria ... 2024" vs
    "Convocatoria ... 2025") compartan la misma entrada de historial_cache.
    """
    if not titulo:
        return ''
    t = unicodedata.normalize('NFKD', titulo).encode('ascii', 'ignore').decode('ascii')
    t = t.lower()
    t = _RE_ANIO.sub('', t)
    t = _RE_NO_ALFANUM.sub(' ', t)
    t = _RE_ESPACIOS.sub(' ', t).strip()
    return t


def _obtener_presupuesto(numero_convocatoria: str):
    """Llamada ligera a /convocatorias solo para obtener el presupuesto.

    Best-effort: si falla, tasa_ejecucion queda en None (score_v9_historial
    ya sabe tratar ese caso), no hace falta abortar toda la resolución de V9
    por esto.
    """
    try:
        resp = requests.get(
            f'{_BDNS_URL}/convocatorias',
            params={'numConv': numero_convocatoria},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get('presupuestoTotal')
    except Exception:
        pass
    return None


def buscar_historial_previo(id_organo_nivel3: int, titulo: str) -> dict:
    """
    Busca en la API de BDNS convocatorias del mismo organismo (nivel3) con la
    misma descripción. La tasa de ejecución se calcula comparando los presupuestos
    de las 2 ediciones más recientes.

    Lanza HistorialBDNSError si la consulta falla (red/timeout/status != 200):
    el llamador (resolverHistorial.py) decide cómo reintentar. Un resultado
    sin coincidencias es válido y se devuelve normalmente (n_similares=0).
    """
    params = {
        'vpd': 'GE',
        'organos': [id_organo_nivel3],
        'descripcion': titulo,
        'descripcionTipoBusqueda': 0,
        'pageSize': 50,
        'page': 0,
    }

    try:
        resp = requests.get(
            f'{_BDNS_URL}/convocatorias/busqueda',
            params=params,
            timeout=10,
        )
    except Exception as e:
        raise HistorialBDNSError(f'error de red buscando historial: {e}') from e

    if resp.status_code != 200:
        raise HistorialBDNSError(f'status {resp.status_code} buscando historial')

    convocatorias = resp.json().get('content', [])

    anios = sorted({
        c['fechaRecepcion'][:4]
        for c in convocatorias
        if c.get('fechaRecepcion')
    })

    tasa = None
    recientes = [c for c in convocatorias if c.get('numeroConvocatoria')][:2]
    if len(recientes) == 2:
        p_actual   = _obtener_presupuesto(recientes[0]['numeroConvocatoria'])
        p_anterior = _obtener_presupuesto(recientes[1]['numeroConvocatoria'])
        if p_actual and p_anterior and p_anterior > 0:
            tasa = round((p_actual / p_anterior) * 100)

    return {
        'n_similares':       len(convocatorias),
        'anios_publicacion': anios,
        'tasa_ejecucion':    tasa,  # >100 = presupuesto creció
    }
