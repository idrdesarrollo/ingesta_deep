"""Resolución de fechas de solicitud a partir de texto libre (textInicio/textFin
de la API BDNS), en español, catalán y gallego.

Sin dependencias de Robocorp ni de la base de datos: solo `re` y `datetime`,
para poder probar los patrones con casos reales de la API sin tocar la BD.
"""
import re
from datetime import datetime, timedelta

# ── Catálogo de meses (castellano + catalán + gallego) ─────────────────────────
_MESES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
    'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
    'gener': 1, 'febrer': 2, 'març': 3, 'marc': 3, 'maig': 5, 'juny': 6,
    'juliol': 7, 'agost': 8, 'setembre': 9, 'novembre': 11, 'desembre': 12,
    'xaneiro': 1, 'febreiro': 2, 'xuño': 6, 'xullo': 7,
}

# ── Patrones de clasificación de textInicio / textFin ─────────────────────────
_RE_NULA = re.compile(
    r'(?i)^(no\s*(existe|hay|tiene|procede|aplica|disponible|se\s*establece|'
    r'se\s*requiere|se\s*contempl|precisa|implica|hay\s*p)|noexiste|'
    r'sin\s*(plazo|fecha|periodo|solicitud|determinar|definir)|'
    r'n[/.]?a\.?|nominativa|convenio\s*directo|concesi[oó]n\s*directa|'
    r'sin\s*inscripci|non\s*se\s*establece|no\s*requiere|no\s*precisa|'
    r'[\s\-\.,\n]*$)',
    re.IGNORECASE
)

_RE_DIA_SIGUIENTE = re.compile(
    r'(?i)(d[ií]a\s*siguiente|siguiente.*d[ií]a|endem[aà]|seguinte|'
    r'd[ií]a\s*posterior|d[ií]a\s*despu[eé]s|siguiente.*publicaci|'
    r'inmediato\s*d[ií]a|primer\s*d[ií]a\s*h[aá]bil\s*(?:siguiente|posterior)|'
    r'd[ií]a\s*h[aá]bil\s*siguiente|publicaci.*siguiente)',
    re.IGNORECASE
)

_RE_X_DIAS = re.compile(
    r'(?i)(?:transcurridos?|comenzar[aá]|comenza|desde\s+el\s+)?'
    r'(\d+)\s*d[ií]as?\s*(h[aá]biles?|naturales?)?\s*'
    r'(?:a\s*partir|desde|despu[eé]s|siguientes?|contados?)?\s*'
    r'(?:del?\s*d[ií]a\s*siguiente\s*)?(?:a|al?|de)?\s*'
    r'(?:la\s*)?publicaci',
    re.IGNORECASE
)

_RE_FECHA_ISO = re.compile(
    r'(?<!\d)(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})(?!\d)'
)

_RE_FECHA_SIN_SEP = re.compile(r'^(\d{2})(\d{2})(\d{4})$')

_RE_FECHA_TEXTO = re.compile(
    r'(?i)(\d{1,2})\s+(?:de\s+)?(enero|febrero|marzo|abril|mayo|junio|julio|agosto|'
    r'septiembre|octubre|noviembre|diciembre|'
    r'gener|febrer|mar[çc]|maig|juny|juliol|agost|setembre|novembre|desembre|'
    r'xaneiro|febreiro|xullo)\s+(?:de\s+)?(\d{4})'
)
_RE_MES_ANO = re.compile(
    r'(?i)^(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|'
    r'octubre|noviembre|diciembre|'
    r'gener|febrer|mar[çc]|maig|juny|juliol|agost|setembre|novembre|desembre)\s+'
    r'(?:de\s+)?(\d{4})$'
)
_RE_EVENTO = re.compile(
    r'(?i)(firma|suscripci[oó]n|convenio|presupuest|aprobaci[oó]n|'
    r'entrada.*vigor|decreto.*concesi|resoluc.*concesi|fecha.*firma)',
    re.IGNORECASE
)

_RE_MESES_PUBLICACION = re.compile(
    r'(?i)(un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|doce|\d+)\s*'
    r'mes(?:es)?\s*(?:naturales?\s*)?'
    r'(?:(?:a\s*partir|desde|despu[eé]s|contados?|siguientes?)\s*'
    r'(?:del?\s*d[ií]a\s*siguiente\s*(?:al?\s*de\s*la?\s*)?)?)?'
    r'\s*(?:la\s*)?publicaci',
)

_RE_DIAS_INICIO = re.compile(
    r'(?i)(\d+)\s*d[ií]as?\s*(?:h[aá]biles?|naturales?)?\s*'
    r'(?:despu[eé]s\s*del?|desde\s*el?|a\s*partir\s*del?|'
    r'contados?\s*(?:a\s*partir\s*del?)?|siguientes?\s*al?)\s*'
    r'(?:la\s*fecha\s*de\s*)?inicio',
)

_RE_MESES_INICIO = re.compile(
    r'(?i)(un|una|dos|tres|cuatro|cinco|seis|\d+)\s*'
    r'mes(?:es)?\s*(?:\(\d+\)\s*)?'
    r'(?:a\s*partir\s*(?:de\s*la?\s*)?(?:fecha\s*de\s*)?|'
    r'desde\s*(?:el?\s*)?(?:la?\s*fecha\s*de\s*)?|'
    r'despu[eé]s\s*del?\s*|'
    r'contados?\s*(?:a\s*partir\s*de\s*la?\s*(?:fecha\s*de\s*)?)?)?\s*'
    r'inicio(?:\s*(?:del?\s*)?(?:solicitud|plazo))?',
)

_NUMEROS_PALABRA = {
    'un': 1, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
    'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10,
    'once': 11, 'doce': 12,
}


def format_fecha(fecha_):
    if fecha_ is not None:
        return datetime.fromisoformat(str(fecha_))
    return None


def _parse_fecha_texto(texto):
    if not texto:
        return None
    t = texto.strip()

    m = _RE_FECHA_SIN_SEP.match(t.replace(' ', ''))
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return datetime(y, mo, d)
        except ValueError:
            pass

    m = _RE_FECHA_ISO.search(t)
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            return datetime(y, mo, d)
        except ValueError:
            pass

    m = _RE_FECHA_TEXTO.search(t)
    if m:
        try:
            d = int(m.group(1))
            mes = _MESES.get(m.group(2).lower())
            y = int(m.group(3))
            if mes:
                return datetime(y, mes, d)
        except (ValueError, KeyError):
            pass

    m = _RE_MES_ANO.match(t)
    if m:
        try:
            mes = _MESES.get(m.group(1).lower())
            y = int(m.group(2))
            if mes:
                return datetime(y, mes, 1)
        except (ValueError, KeyError):
            pass

    return None


def _add_months(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days_in = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return dt.replace(year=year, month=month, day=min(dt.day, days_in[month - 1]))


def _resolver_relativo_publicacion(texto, fecha_recepcion):
    m = _RE_X_DIAS.search(texto)
    if m:
        try:
            n = int(m.group(1))
            es_habil = bool(re.search(r'h[aá]bil', texto, re.IGNORECASE))
            offset = int(n * 1.4) if es_habil else n
            return fecha_recepcion + timedelta(days=offset), True
        except (ValueError, AttributeError):
            pass

    m = _RE_MESES_PUBLICACION.search(texto)
    if m:
        try:
            raw = m.group(1).lower()
            n = _NUMEROS_PALABRA.get(raw) or int(raw)
            return _add_months(fecha_recepcion, n), True
        except (ValueError, AttributeError, KeyError):
            pass

    if _RE_DIA_SIGUIENTE.search(texto):
        return fecha_recepcion + timedelta(days=1), True

    return None, False


def resolver_fechas(json_, fecha_recepcion, ahora=None):
    """Resuelve fechaInicio/fechaFin y el estado de vigencia de una convocatoria.

    `ahora` es inyectable (por defecto `datetime.now()` en el momento de la
    llamada, no en el de importar el módulo) para poder fijar la fecha "hoy"
    en los tests sin depender del reloj de la máquina.
    """
    if ahora is None:
        ahora = datetime.now()

    text_inicio = json_.get('textInicio')
    text_fin = json_.get('textFin')
    flag_abierto = json_.get('abierto', False)
    descripcion = (json_.get('descripcion') or '').strip()

    def _texto_util(t):
        return t and t != descripcion and not _RE_NULA.match(t) and not _RE_EVENTO.search(t)

    fechaInicio = format_fecha(json_.get('fechaInicioSolicitud'))
    inicio_estimado = False

    if fechaInicio is None and text_inicio:
        t_ini = text_inicio.strip()
        if _texto_util(t_ini):
            fecha_parseada = _parse_fecha_texto(t_ini)
            if fecha_parseada:
                fechaInicio = fecha_parseada
            else:
                fechaInicio, inicio_estimado = _resolver_relativo_publicacion(t_ini, fecha_recepcion)

    fechaFin = format_fecha(json_.get('fechaFinSolicitud'))
    fin_estimado = False

    if fechaFin is None and text_fin:
        t_fin = text_fin.strip()
        if _texto_util(t_fin):
            fecha_parseada = _parse_fecha_texto(t_fin)
            if fecha_parseada:
                fechaFin = fecha_parseada
                fin_estimado = True
            else:
                fechaFin, fin_estimado = _resolver_relativo_publicacion(t_fin, fecha_recepcion)

            if fechaFin is None and fechaInicio is not None:
                m = _RE_DIAS_INICIO.search(t_fin)
                if m:
                    try:
                        n = int(m.group(1))
                        es_habil = bool(re.search(r'h[aá]bil', t_fin, re.IGNORECASE))
                        offset = int(n * 1.4) if es_habil else n
                        fechaFin = fechaInicio + timedelta(days=offset)
                        fin_estimado = True
                    except (ValueError, AttributeError):
                        pass
                else:
                    m = _RE_MESES_INICIO.search(t_fin)
                    if m:
                        try:
                            raw = m.group(1).lower()
                            n = _NUMEROS_PALABRA.get(raw) or int(raw)
                            fechaFin = _add_months(fechaInicio, n)
                            fin_estimado = True
                        except (ValueError, AttributeError, KeyError):
                            pass

    if flag_abierto and fechaInicio is None and fechaFin is None:
        estado = 'Indefinida'
    elif fechaFin is not None:
        if fechaInicio is not None and ahora < fechaInicio:
            estado = 'Pendiente'
        elif ahora <= fechaFin:
            estado = 'Abierta'
        else:
            estado = 'Cerrada'
    elif flag_abierto:
        estado = 'Indefinida'
    else:
        estado = 'SinDatos'

    return {
        'fechaInicio':        fechaInicio,
        'fechaFin':           fechaFin,
        'inicioEsEstimado':   inicio_estimado,
        'finEsEstimado':      fin_estimado,
        'estadoVigencia':     estado,
        'textInicioOriginal': text_inicio,
        'textFinOriginal':    text_fin,
    }
