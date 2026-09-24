"""
Motor de scoring sin LLM — Reglas estructuradas + Keywords.

- V1:  tramitación estandarizada — grado de estandarización del proceso (keywords + instrumento) #TODO: LISTO
- V2:  volumen de beneficiarios potenciales (tipo + alcance geográfico) #TODO: LISTO
- V3:  cuantía del presupuesto total #TODO: LISTO
- V4:  fortaleza del canal intermediario (keywords + tipo de beneficiario) #TODO: LISTO
- V5:  presupuesto recurrente — longevidad del programa (keywords fondos + presupuesto) #TODO: LISTO
- V6:  cultura de gestión de subvenciones del sector (tipo beneficiario + keywords) #TODO: LISTO
- V7:  requisitos técnicos y certificaciones (keywords)  #TODO: LISTO
- V8:  plazo de cobro estimado (keywords + instrumento BDNS) #TODO: LISTO
- V9:  historial de recurrencia (llamada a la API BDNS del organismo) #TODO: LISTO

Punto de entrada externo: calcular_y_guardar(dat, db)
    Llamar desde datBBDD.databaseInsert() tras el UPSERT principal.

V9 es la excepción: requiere 1-3 llamadas HTTP a BDNS por convocatoria
(keywords/historial.py). Para que la ingesta nunca dependa de esa latencia,
este módulo NO llama a la red: calcular_y_guardar() guarda V9 provisional
(=1) con v9_estado='pendiente'/'sin_organo', y resolverHistorial.py (proceso
aparte, por lotes, con throttling) resuelve el historial real después y
llama a recalcular_con_historial() para completar la fila. Por eso scorer.py
no importa `requests`: es un motor de reglas 100% offline.
"""

import json
from datetime import date, datetime, timedelta
from keywords.db import Database


# =============================================================================
# CONSTANTES DEL MOTOR
# =============================================================================

PESOS_DEFAULT: dict = {
    'v1_tramitacion_estandarizada': 1.0,
    'v2_volumen':                   1.2,
    'v3_cuantia':                   1.0,
    'v4_intermediarios':            1.4,
    'v5_presupuesto_recurrente':    1.1,
    'v6_cultura_gestion':           1.1,
    'v7_req_tecnicos':              1.3,
    'v8_plazo_cobro':               1.4,
    'v9_historial':                 1.3,
}
UMBRALES_DEFAULT: dict = {'azul': 80, 'oportunidad': 65, 'revisar': 50}
MAXIMO_POSIBLE: float = sum(5 * p for p in PESOS_DEFAULT.values())  # 54.0


# =============================================================================
# CRITERIOS V1 – V9
# =============================================================================

# -----------------------------------------------------------------------------
# V1 — Tramitación estandarizada
# -----------------------------------------------------------------------------

# Keywords validadas contra 23.834 registros reales BDNS (descripcion + basesReguladoras)
_KW_V1_TOTALMENTE: list = [         # → 5: 100% expedientes mismo flujo
    'bono', 'cheque',
]
_KW_V1_POCO_ESTANDARIZADO: list = [ # → 1: cada expediente requiere análisis único
    'i+d',
    'investigación y desarrollo', 'investigacion y desarrollo',
    'proyecto de investigación', 'proyecto de investigacion',
    'memoria técnica', 'memoria tecnica',
    'proyecto técnico', 'proyecto tecnico',
]

# Campo estructurado tipoConvocatoria → más fiable que keywords de texto para niveles 3-4
_V1_TIPO_CONVOCATORIA: dict = {
    'Concesión directa - instrumental': 3,
    'Concurrencia competitiva - canónica': 4,
    'Concesión directa - canónica': 4
}

# Instrumento BDNS como fallback final
_V1_INSTRUMENTO_ESTANDARIZACION: dict = {
    'SUBVENCIÓN Y ENTREGA DINERARIA SIN CONTRAPRESTACIÓN': 3,
    'PRÉSTAMO':                                            2,
    'GARANTÍA':                                            2,
    'VENTAJA FISCAL':                                      4,
    'APORTACIÓN DE FINANCIACIÓN RIESGO':                   1,
    'OTROS INSTRUMENTOS DE AYUDA':                         2,
}


def score_v1_tramitacion_estandarizada(descripcion: str, bases: str,
                                        instrumentos_desc: list,
                                        tipo_convocatoria: str) -> int:
    """
    Grado de estandarización del proceso de tramitación.
    Mayor puntuación = más expedientes siguen el mismo flujo = más escalable.

    Capa 1: keywords de flujo totalmente estandarizado (bonos, cheques)   → 5
    Capa 2: keywords de baja estandarización (I+D, memoria técnica)       → 1
    Capa 3: campo estructurado tipoConvocatoria                           → 3 ó 4
    Capa 4: instrumento BDNS como proxy                                    → según mapa
    Capa 5: default conservador                                            → 3
    """
    texto = (descripcion + ' ' + (bases or '')).lower()

    if any(k in texto for k in _KW_V1_TOTALMENTE):
        return 5
    if any(k in texto for k in _KW_V1_POCO_ESTANDARIZADO):
        return 1

    if tipo_convocatoria:
        score = _V1_TIPO_CONVOCATORIA.get(tipo_convocatoria.strip().lower())
        if score:
            return score

    if instrumentos_desc:
        scores = [_V1_INSTRUMENTO_ESTANDARIZACION.get(i.upper().strip(), 3)
                  for i in instrumentos_desc]
        return max(scores)

    return 3


# -----------------------------------------------------------------------------
# V2 — Volumen de beneficiarios potenciales
# -----------------------------------------------------------------------------

_V2_VOLUMEN_BENEFICIARIO: dict = {
    'GRAN EMPRESA':                                                 1,  # ~5.000 empresas
    'PERSONAS JURÍDICAS QUE NO DESARROLLAN ACTIVIDAD ECONÓMICA':   2,  # ~300.000 ONGs/fundaciones
    'SIN INFORMACION ESPECIFICA':                                   3,  # indeterminado
    'PYME Y PERSONAS FÍSICAS QUE DESARROLLAN ACTIVIDAD ECONÓMICA': 4,  # ~6M pymes+autónomos
    'PERSONAS FÍSICAS QUE NO DESARROLLAN ACTIVIDAD ECONÓMICA':     5,  # ~47M ciudadanos
}


def score_v2_volumen(beneficiarios_desc: list, n_regiones: int) -> int:
    """
    Volumen de beneficiarios potenciales.
    Combina el tipo de beneficiario BDNS con el alcance geográfico.
    Si vienen varios tipos, toma el más favorable (mayor volumen).
    """
    if not beneficiarios_desc:
        return 3   # sin restricción → volumen indeterminado, neutro

    base = max(
        (_V2_VOLUMEN_BENEFICIARIO.get(b.strip().upper(), 3) for b in beneficiarios_desc),
        default=3,
    )

    if n_regiones >= 15:  base = min(5, base + 1)   # alcance nacional
    elif n_regiones == 1: base = max(1, base - 1)   # solo una región

    return base


# -----------------------------------------------------------------------------
# V3 — Cuantía del presupuesto total
# -----------------------------------------------------------------------------

def score_v3_cuantia(presupuesto) -> int:
    """Tamaño del presupuesto total disponible."""
    if presupuesto is None:
        return 1
    p = float(presupuesto)
    if p >= 1_000_000:  return 5
    if p >= 200_000:    return 4
    if p >= 50_000:     return 3
    if p >= 10_000:     return 2
    return 1


# -----------------------------------------------------------------------------
# V4 — Entidades intermediarias
# -----------------------------------------------------------------------------

# Keywords validadas contra 23.834 descripciones reales BDNS
_KW_V4_CANAL_EXCELENTE: list = [    # → 5: una entidad cubre +50% de beneficiarios
    'kit digital', 'acelera pyme', 'agente digitalizador',
    'entidad colaboradora acreditada', 'red.es',
]

_KW_V4_CANAL_FUERTE: list = [       # → 4: red con acceso a miles de socios
    'confederación', 'confederacion',
    'asociaciones empresariales',
    'consejo superior',
    'colegios oficiales',
]

_KW_V4_CANAL_MEDIO: list = [        # → 3: 3-5 entidades con base de contactos activa
    'cámara de comercio', 'camara de comercio',
    'cámara agraria', 'camara agraria',
    'federación empresarial', 'federacion empresarial',
    'asociación empresarial', 'asociacion empresarial',
    'asociación de empresas', 'asociacion de empresas',
    'colegio profesional', 'gremio',
    'agrupación empresarial', 'agrupacion empresarial',
]

_V4_BASE_BENEFICIARIO: dict = {     # fallback cuando no hay keywords en texto
    'GRAN EMPRESA':                                                 2,
    'PERSONAS FÍSICAS QUE NO DESARROLLAN ACTIVIDAD ECONÓMICA':     2,
    'PERSONAS JURÍDICAS QUE NO DESARROLLAN ACTIVIDAD ECONÓMICA':   3,
    'SIN INFORMACION ESPECIFICA':                                   3,
    'PYME Y PERSONAS FÍSICAS QUE DESARROLLAN ACTIVIDAD ECONÓMICA': 3,
}


def score_v4_intermediarios(descripcion: str, bases: str,
                             beneficiarios_desc: list) -> int:
    """
    Fortaleza del canal aglutinador en el lado del beneficiario.
    Mayor puntuación = un intermediario cubre más beneficiarios potenciales de un golpe.

    Capa 1: keywords en descripción + bases (señal explícita)
    Capa 2: tipo de beneficiario BDNS (fallback estructural)
    Capa 3: default conservador → 2
    """
    texto = (descripcion + ' ' + (bases or '')).lower()

    if any(k in texto for k in _KW_V4_CANAL_EXCELENTE):
        return 5
    if any(k in texto for k in _KW_V4_CANAL_FUERTE):
        return 4
    if any(k in texto for k in _KW_V4_CANAL_MEDIO):
        return 3

    if beneficiarios_desc:
        return max(
            (_V4_BASE_BENEFICIARIO.get(b.strip().upper(), 3) for b in beneficiarios_desc),
            default=3,
        )

    return 2


# -----------------------------------------------------------------------------
# V5 — Presupuesto recurrente
# -----------------------------------------------------------------------------

# Keywords validadas contra 23.834 registros reales BDNS (descripcion + basesReguladoras)
_KW_V5_ESTRUCTURAL: list = [    # → 5: >7 años, >500M€, renovación automática
    'next generation',
    'plan de recuperación', 'plan de recuperacion',
    'prtr', 'mrr',
    'mecanismo de recuperación', 'mecanismo de recuperacion',
]
_KW_V5_LARGO_PLAZO: list = [    # → 4: 4-7 años, fondos europeos con período definido
    'feder', 'feader',
    'fse', 'fondo social europeo',
    'fondo europeo',
    '2021-2027',
    'fondo de cohesión', 'fondo de cohesion',
    'fondo de transición justa', 'fondo de transicion justa',
    'programa life', 'proyecto life',
    'interreg',
    'horizonte europa',
]
_KW_V5_MEDIO_PLAZO: list = [    # → 3: 3-4 años garantizados
    'plurianual', 'programa marco',
]
_KW_V5_PUNTUAL: list = [        # → 1: grant explícitamente no recurrente
    'carácter excepcional', 'caracter excepcional',
    'subvención excepcional', 'subvencion excepcional',
    'carácter extraordinario', 'caracter extraordinario',
    'carácter puntual', 'caracter puntual',
    'convocatoria única', 'convocatoria unica',
]


def score_v5_presupuesto_recurrente(descripcion: str, bases: str,
                                     presupuesto) -> int:
    """
    Longevidad y tamaño del programa de financiación.
    Mayor puntuación = programa más estable y longevo = más oportunidades recurrentes.

    Capa 1: keywords de programas estructurales (Next Generation, PRTR, MRR) → 5
    Capa 2: keywords de fondos europeos con período plurianual definido       → 4
    Capa 3: keywords de programas plurianuales nacionales/autonómicos         → 3
    Capa 4: keywords de grants explícitamente no recurrentes                  → 1
    Capa 5: presupuesto anual de la convocatoria como proxy                   → escala 1-5
    """
    texto = (descripcion + ' ' + (bases or '')).lower()

    if any(k in texto for k in _KW_V5_ESTRUCTURAL):
        return 5
    if any(k in texto for k in _KW_V5_LARGO_PLAZO):
        return 4
    if any(k in texto for k in _KW_V5_MEDIO_PLAZO):
        return 3
    if any(k in texto for k in _KW_V5_PUNTUAL):
        return 1

    if presupuesto is None:
        return 1
    p = float(presupuesto)
    if p >= 500_000_000: return 5
    if p >= 100_000_000: return 4
    if p >=  10_000_000: return 3
    if p >=   1_000_000: return 2
    return 1


# -----------------------------------------------------------------------------
# V6 — Cultura de gestión de subvenciones del sector
# -----------------------------------------------------------------------------

# Keywords que indican sector con ALTA cultura → score bajo
_KW_V6_ALTA_CULTURA: list = [  # TODO: revisar y ampliar según el sector de la empresa
    'i+d', 'investigación', 'investigacion',
    'desarrollo tecnológico', 'desarrollo tecnologico',
    'innovación', 'innovacion',
    'startup', 'spin-off', 'spin off',
    'transferencia tecnológica', 'transferencia tecnologica',
    'inteligencia artificial', 'ciberseguridad',
    'biotecnología', 'biotecnologia',
    'ensayo clínico', 'ensayo clinico',
    'patente',
]

# Sectores CNAE con cultura histórica media-alta de subvenciones
_V6_SECTORES_CULTURA_ALTA: set = {'A', 'F'}  # TODO: revisar y ampliar según el sector de la empresa

# Instrumentos financieros sofisticados → audiencia con mayor cultura financiera
_V6_INSTRUMENTOS_CULTURA_ALTA: set = {        # TODO: revisar y ampliar según el sector de la empresa
    'PRÉSTAMO',
    'APORTACIÓN DE FINANCIACIÓN RIESGO',
    'GARANTÍA',
    'VENTAJA FISCAL',
}


def score_v6_cultura_gestion(dat: dict) -> int:
    """
    Poca cultura de gestión: cuanto menos familiarizado está el sector
    con las ayudas públicas, mayor la puntuación (mejor oportunidad consultora).

    Base desde tipo de beneficiario, ajustada por sector CNAE, keywords de
    I+D en la descripción e instrumento financiero.
    """
    texto        = (dat.get('descripcion', '') + ' ' +
                    dat.get('descripcionBasesReguladoras', '')).lower()
    beneficiarios = [b['descripcion'].strip().upper()
                     for b in dat.get('tiposBeneficiarios', [])]
    sectores      = {s['codigo'].upper() for s in dat.get('sectores', [])}
    instrumentos  = {i['descripcion'].strip().upper()
                     for i in dat.get('instrumentos', [])}

    if 'GRAN EMPRESA' in beneficiarios:
        base = 1   # siempre tienen dpto. de subvenciones
    elif 'PERSONAS FÍSICAS QUE NO DESARROLLAN ACTIVIDAD ECONÓMICA' in beneficiarios:
        base = 5   # ciudadanos: sin cultura de gestión de ayudas
    elif 'PERSONAS JURÍDICAS QUE NO DESARROLLAN ACTIVIDAD ECONÓMICA' in beneficiarios:
        base = 4   # ONGs/fundaciones: poca cultura empresarial de subvenciones
    elif not beneficiarios:
        base = 3   # sin dato → neutro
    else:
        base = 3   # PYME / sin info específica → neutro, se ajusta por sector

    if any(k in texto for k in _KW_V6_ALTA_CULTURA):
        base = max(1, base - 1)

    if _V6_SECTORES_CULTURA_ALTA & sectores:
        base = max(1, base - 1)

    if _V6_INSTRUMENTOS_CULTURA_ALTA & instrumentos:
        base = max(1, base - 1)

    return base


# -----------------------------------------------------------------------------
# V7 — Requisitos técnicos y certificaciones
# -----------------------------------------------------------------------------

_KW_V7_REQ_TECNICOS: list = [
    'iso 9001', 'iso 14001', 'iso 27001', 'emas',
    'titulación universitaria', 'titulacion universitaria',
    'máster oficial', 'master oficial', 'doctorado',
    'experiencia mínima', 'experiencia minima',
    'años de experiencia', 'anos de experiencia',
    'capacidad técnica acreditada', 'capacidad tecnica acreditada',
    'homologación', 'homologacion',
]


def score_v7_req_tecnicos(descripcion: str, bases: str) -> int:
    """Exigencia de certificaciones, titulaciones y acreditaciones técnicas."""
    texto = (descripcion + ' ' + (bases or '')).lower()
    n = sum(1 for k in _KW_V7_REQ_TECNICOS if k in texto)
    if n == 0: return 5
    if n == 1: return 4
    if n == 2: return 3
    if n == 3: return 2
    return 1


# -----------------------------------------------------------------------------
# V8 — Plazo de cobro
# -----------------------------------------------------------------------------

# Mapa instrumento BDNS → plazo de cobro estimado (fallback sin keywords)
_V8_INSTRUMENTO_SCORES: dict = {
    'SUBVENCIÓN Y ENTREGA DINERARIA SIN CONTRAPRESTACIÓN': 3,  # 6-12m con justificación habitual
    'PRÉSTAMO':                                            2,
    'GARANTÍA':                                            2,
    'VENTAJA FISCAL':                                      4,  # cobro rápido vía declaración fiscal
    'APORTACIÓN DE FINANCIACIÓN RIESGO':                   1,
    'OTROS INSTRUMENTOS DE AYUDA':                         2,
}

# Keywords validadas contra 23.834 descripciones reales BDNS
_KW_V8_INMEDIATO: list = [      # → 5: cobro antes de ejecutar/justificar
    'pago anticipado', 'abono anticipado', 'anticipo de pago',
    'cobro anticipado', 'pago por adelantado',
]
_KW_V8_CORTO: list = [          # → 4: cobro tras resolución sin justificación previa
    'justificacion posterior', 'justificación posterior',
    'pago a la resolucion', 'pago a la resolución',
    'pago a la concesion', 'pago a la concesión',
    'pago a la notificacion', 'pago a la notificación',
    'pago a la firma',
]
_KW_V8_LARGO: list = [          # → 2: justificas antes de cobrar o pagos plurianuales
    'justificacion previa', 'justificación previa',
    'previo al pago', 'previa al pago',
    'anualidades', 'plurianual', 'en varios ejercicios',
]


def score_v8_plazo_cobro(instrumentos_desc: list,
                          descripcion: str, bases: str) -> int:
    """
    Tiempo estimado desde gestión hasta primer cobro.

    Capa 1: keywords de pago anticipado en texto             → 5
    Capa 2: keywords de pago a resolución / posterior        → 4
    Capa 3: keywords de plazo largo (plurianual, previa)     → 2
    Capa 4: instrumento BDNS como proxy                      → según mapa
    Capa 5: default conservador                              → 2
    """
    texto = (descripcion + ' ' + (bases or '')).lower()

    if any(k in texto for k in _KW_V8_INMEDIATO):
        return 5
    if any(k in texto for k in _KW_V8_CORTO):
        return 4
    if any(k in texto for k in _KW_V8_LARGO):
        return 2

    if not instrumentos_desc:
        return 2
    scores = [_V8_INSTRUMENTO_SCORES.get(i.upper().strip(), 2) for i in instrumentos_desc]
    return max(scores)


# -----------------------------------------------------------------------------
# V9 — Historial de recurrencia
#
# La resolución real (llamadas HTTP a BDNS) vive en keywords/historial.py y
# la ejecuta resolverHistorial.py, nunca este módulo. Aquí solo queda la
# función pura que convierte un `historial` dict ya resuelto en una nota 1-5.
# -----------------------------------------------------------------------------

def _historial_vacio() -> dict:
    """Historial 'provisional' usado en la ingesta: sin red, conservador."""
    return {'n_similares': 0, 'anios_publicacion': [], 'tasa_ejecucion': None}


def score_v9_historial(historial: dict) -> int:
    """Recurrencia histórica de la convocatoria."""
    n       = historial.get('n_similares', 0)
    tasa    = historial.get('tasa_ejecucion')
    anios   = historial.get('anios_publicacion', [])
    n_anios = len(anios)

    if n == 0:
        return 1
    if n_anios == 1:
        return 2
    if n_anios == 2 and tasa is not None and tasa < 70:
        return 2
    if n_anios >= 4 and tasa is not None and tasa >= 100:
        return 5   # estructural: 4+ años, presupuesto creciente
    if n_anios >= 3 and (tasa is None or tasa >= 90):
        return 4   # recurrente: 3+ años, tasa ≥90% o sin dato
    if n_anios >= 2 and (tasa is None or tasa >= 70):
        return 3   # regular: 2+ años, tasa ≥70% o sin dato
    return 2


# =============================================================================
# CÁLCULO COMPLETO Y CLASIFICACIÓN
# =============================================================================

def clasificar(score_pct: float, umbrales: dict) -> str:
    if score_pct >= umbrales['azul']:        return 'azul'
    if score_pct >= umbrales['oportunidad']: return 'oportunidad'
    if score_pct >= umbrales['revisar']:     return 'revisar'
    return 'descartar'


def _calcular_score(criterios: dict, pesos: dict, umbrales: dict) -> dict:
    """Puntos/porcentaje/nivel a partir de los 9 criterios ya calculados.

    Compartido por calcular_score_completo() (ingesta) y
    recalcular_con_historial() (resolución diferida de V9), para que ambos
    caminos apliquen exactamente la misma fórmula.
    """
    maximo    = sum(5 * p for p in pesos.values())
    score_raw = round(sum(criterios[k] * pesos[k] for k in pesos), 2)
    score_pct = round((score_raw / maximo) * 100, 1)
    return {
        'score_raw': score_raw,
        'score_pct': score_pct,
        'nivel':     clasificar(score_pct, umbrales),
    }


def calcular_score_completo(dat: dict, pesos: dict, umbrales: dict,
                             historial: dict) -> dict:
    """
    Calcula los 9 criterios y el score final a partir del dict raw de la API BDNS.

    Retorna:
        criterios   — dict {'v1': int, ..., 'v9': int}
        score_raw   — puntos absolutos
        score_pct   — porcentaje sobre el máximo (0-100)
        nivel       — 'azul' | 'oportunidad' | 'revisar' | 'descartar'
    """
    instrumentos  = [i['descripcion'] for i in dat.get('instrumentos', [])]
    beneficiarios = [b['descripcion'] for b in dat.get('tiposBeneficiarios', [])]
    sectores      = [s['codigo']      for s in dat.get('sectores', [])]
    n_regiones    = len(dat.get('regiones', []))
    descripcion   = dat.get('descripcion', '')
    bases         = dat.get('descripcionBasesReguladoras', '')

    criterios = {
        'v1_tramitacion_estandarizada': score_v1_tramitacion_estandarizada(
            descripcion, bases,
            instrumentos,
            dat.get('tipoConvocatoria'),
        ),
        'v2_volumen':                score_v2_volumen(beneficiarios, n_regiones),
        'v3_cuantia':                score_v3_cuantia(dat.get('presupuestoTotal')),
        'v4_intermediarios':         score_v4_intermediarios(descripcion, bases, beneficiarios),
        'v5_presupuesto_recurrente': score_v5_presupuesto_recurrente(
            descripcion, bases,
            dat.get('presupuestoTotal'),
        ),
        'v6_cultura_gestion':        score_v6_cultura_gestion(dat),
        'v7_req_tecnicos':           score_v7_req_tecnicos(descripcion, bases),
        'v8_plazo_cobro':            score_v8_plazo_cobro(instrumentos, descripcion, bases),
        'v9_historial':              score_v9_historial(historial),
    }

    return {'criterios': criterios, **_calcular_score(criterios, pesos, umbrales)}


def recalcular_con_historial(criterios: dict, historial: dict,
                              pesos: dict, umbrales: dict) -> dict:
    """
    Completa una fila de scoring_results una vez resuelto el historial real.

    `criterios` son los v1-v8 YA guardados en esa fila (se reutilizan tal
    cual, no se recalculan) y `pesos` debe ser el pesos_snapshot de esa
    misma fila — no la config activa actual, que pudo cambiar mientras la
    convocatoria esperaba en la cola de resolución. `umbrales` sí usa la
    config activa (no hay umbrales_snapshot en el esquema).

    Llamada desde resolverHistorial.py, nunca desde la ingesta.
    """
    criterios = dict(criterios)
    criterios['v9_historial'] = score_v9_historial(historial)
    return {'criterios': criterios, **_calcular_score(criterios, pesos, umbrales)}


# =============================================================================
# ACCESO A BD
# =============================================================================

def cargar_config_activa(db: Database) -> tuple:
    """
    Carga pesos y umbrales de la configuración activa en scoring_config.
    Si no existe configuración activa, devuelve los valores por defecto.
    """
    try:
        result = db.query(
            "SELECT pesos, umbrales FROM scoring_config WHERE activa = TRUE LIMIT 1"
        )
        if result:
            pesos    = result[0][0] if isinstance(result[0][0], dict) else json.loads(result[0][0])
            umbrales = result[0][1] if isinstance(result[0][1], dict) else json.loads(result[0][1])
            return pesos, umbrales
    except Exception:
        pass
    return PESOS_DEFAULT.copy(), UMBRALES_DEFAULT.copy()


def calcular_y_guardar(dat: dict, db: Database) -> None:
    """
    Punto de entrada principal, llamado por convocatoria durante la ingesta.
    1. Carga config activa de scoring_config.
    2. Calcula V1-V8 (sin red) y deja V9 provisional (=1, conservador),
       con v9_estado='pendiente'.
    3. Persiste en scoring_results y actualiza convocatorias.score_pct con
       el score provisional.

    NO consulta BDNS para V9: eso lo hace resolverHistorial.py después, por
    lotes, usando organos.id_bdns_nivel3 (persistido en buscar_organo_id()
    dentro de datBBDD.databaseInsert(), antes de llamar aquí). Si ese ID no
    se pudo resolver, es resolverHistorial.py -no esta función- quien pasa
    la fila a v9_estado='sin_organo' mediante un JOIN, sin gastar llamadas a
    BDNS ni volver a tocar texto de organismos.

    Ante cualquier error interno no interrumpe el flujo de ingesta.
    """
    try:
        conv_id = dat['id']
        pesos, umbrales = cargar_config_activa(db)

        resultado  = calcular_score_completo(dat, pesos, umbrales, _historial_vacio())
        criterios  = resultado['criterios']
        pesos_snap = json.dumps(pesos)
        v9_estado  = 'pendiente'

        db.query(
            "UPDATE scoring_results SET es_vigente = FALSE "
            "WHERE convocatoria_id = %s AND es_vigente = TRUE",
            data=(conv_id,)
        )

        db.query(
            '''INSERT INTO scoring_results (
                convocatoria_id,
                v1_tramitacion_estandarizada, v2_volumen, v3_cuantia,
                v4_intermediarios, v5_presupuesto_recurrente, v6_cultura_gestion,
                v7_req_tecnicos, v8_plazo_cobro, v9_historial,
                score_raw, score_pct, nivel_asignado,
                pesos_snapshot, v9_estado, es_vigente
            ) VALUES (
                %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, TRUE
            )''',
            data=(
                conv_id,
                criterios['v1_tramitacion_estandarizada'],
                criterios['v2_volumen'],
                criterios['v3_cuantia'],
                criterios['v4_intermediarios'],
                criterios['v5_presupuesto_recurrente'],
                criterios['v6_cultura_gestion'],
                criterios['v7_req_tecnicos'],
                criterios['v8_plazo_cobro'],
                criterios['v9_historial'],
                resultado['score_raw'], resultado['score_pct'],
                resultado['nivel'], pesos_snap, v9_estado,
            )
        )

        db.query(
            "UPDATE convocatorias SET score_pct = %s, nivel_scoring = %s, procesada = TRUE WHERE id = %s",
            data=(resultado['score_pct'], resultado['nivel'], conv_id)
        )

    except Exception as e:
        print(f'[scorer] Error calculando score para conv {dat.get("id")}: {e}')


# =============================================================================
# UTILIDADES INTERNAS
# =============================================================================

def _as_date(valor):
    """Convierte datetime, date o string ISO a date. Devuelve None si no es posible."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor)).date()
    except (ValueError, TypeError):
        return None
