import requests
import re
from datetime import datetime
from keywords.db import Database, conectar
from variables import variables_
from keywords import get_organo
from keywords import scorer as _scorer
from keywords import embedder as _embedder
from keywords.resolucion_fechas import format_fecha, resolver_fechas
URLAPI = 'https://www.infosubvenciones.es/bdnstrans/api/'

_df_organos = None


def _get_df_organos():
    """Carga (y cachea) el catálogo de órganos BDNS. Perezoso: la 1ª llamada
    real hace las peticiones HTTP; las siguientes reutilizan el resultado."""
    global _df_organos
    if _df_organos is None:
        _df_organos = get_organo.main()
    return _df_organos


def json_documentos(documentos, codigoBDNS):
    lista_json = []
    if len(documentos) > 0:
        for documento in documentos:
            id = documento["id"]
            json_doc = {}
            json_doc['id_document'] = documento["id"]
            json_doc['name_document'] = documento["nombreFic"]
            url = f'https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/{codigoBDNS}/document/{id}'
            json_doc['enlaceDocumento'] = url
            lista_json.append(json_doc)
    return lista_json


def presupuesto(presupuestoTotal):
    if presupuestoTotal is None:
        return None
    miles_translator = str.maketrans(".,", ",.")
    numero = f"{presupuestoTotal:,}".translate(miles_translator)
    numero += " €"
    return numero


def is_date(texto):
    if texto and re.match(variables_.patrones[0], texto):
        date = datetime.strptime(texto, '%d/%m/%Y')
        return True, date
    return False, ''


# ── PostgreSQL: helpers de catálogo ───────────────────────────────────────────

def _to_pg_int_array(lst):
    """Convierte una lista Python a literal de array PostgreSQL: '{1,2,3}'."""
    if not lst:
        return '{}'
    return '{' + ','.join(str(x) for x in lst) + '}'


def buscar_organo_id(db: Database, nivel1, nivel2, nivel3):
    """Busca o inserta un órgano en la tabla unificada y devuelve su id SERIAL.

    De paso resuelve y persiste id_bdns_nivel3 -el ID numérico que BDNS usa
    para este órgano en su propio catálogo, necesario para /convocatorias/
    busqueda?organos=X en V9- la primera vez que se ve este órgano. Si un
    intento anterior no pudo resolverlo (quedó NULL), lo reintenta aquí
    también: es barato, _get_df_organos() ya está cacheado para esta corrida.
    Así resolverHistorial.py solo hace un JOIN, sin volver a tocar texto de
    organismos ni llamar a /organos.
    """
    n1 = nivel1 or ''
    n2 = nivel2 or ''
    n3 = nivel3 or ''
    result = db.query(
        "SELECT id, id_bdns_nivel3 FROM organos "
        "WHERE nivel1 = %s AND COALESCE(nivel2,'') = %s AND COALESCE(nivel3,'') = %s",
        data=(n1, n2, n3)
    )
    if result:
        organo_id, id_bdns_nivel3 = result[0]
        if id_bdns_nivel3 is None:
            id_bdns_nivel3 = get_id_organo_bdns(nivel3)
            if id_bdns_nivel3 is not None:
                db.query(
                    "UPDATE organos SET id_bdns_nivel3 = %s WHERE id = %s",
                    data=(id_bdns_nivel3, organo_id)
                )
        return organo_id

    tipo = 4
    for n in [nivel3, nivel2, nivel1]:
        if n:
            df_organos = _get_df_organos()
            fila = df_organos[df_organos['organo'] == n]
            if len(fila) > 0:
                tipo = variables_.dict_tipe.get(fila['tipe'].values[0], 4)
                break

    id_bdns_nivel3 = get_id_organo_bdns(nivel3)

    db.query(
        "INSERT INTO organos (nivel1, nivel2, nivel3, tipo, id_bdns_nivel3) "
        "VALUES (%s, %s, %s, %s, %s)",
        data=(n1, nivel2, nivel3, tipo, id_bdns_nivel3)
    )
    result = db.query(
        "SELECT id FROM organos "
        "WHERE nivel1 = %s AND COALESCE(nivel2,'') = %s AND COALESCE(nivel3,'') = %s",
        data=(n1, n2, n3)
    )
    return result[0][0] if result else None


def buscar_o_insertar_catalogo(db: Database, tipo, codigo, nombre):
    """Busca o inserta un item de catálogo y devuelve su id SERIAL."""
    codigo_str = str(codigo)
    result = db.query(
        "SELECT id FROM catalogo_items WHERE tipo = %s AND codigo = %s",
        data=(tipo, codigo_str)
    )
    if result:
        return result[0][0]
    db.query(
        "INSERT INTO catalogo_items (tipo, codigo, nombre) VALUES (%s, %s, %s)",
        data=(tipo, codigo_str, nombre)
    )
    result = db.query(
        "SELECT id FROM catalogo_items WHERE tipo = %s AND codigo = %s",
        data=(tipo, codigo_str)
    )
    return result[0][0] if result else None


def _get_sectores_ids(db: Database, sectores):
    ids = []
    for s in sectores:
        id_ = buscar_o_insertar_catalogo(db, 'sector', s['codigo'], s['descripcion'])
        if id_:
            ids.append(id_)
    return ids


def _get_regiones_ids(db: Database, regiones):
    """
    La API devuelve regiones como {"descripcion": "ES70 - CANARIAS"}.
    Parseamos el código NUTS (ES70) y el nombre, y los insertamos en
    catalogo_items con tipo='region' igual que el resto de catálogos.
    """
    ids = []
    for r in regiones:
        desc = r.get('descripcion', '')
        if not desc:
            continue
        partes = desc.split(' - ', 1)
        codigo = partes[0].strip()          # ej. "ES70"
        nombre = partes[1].strip() if len(partes) > 1 else codigo
        id_ = buscar_o_insertar_catalogo(db, 'region', codigo, nombre)
        if id_:
            ids.append(id_)
    return ids


def _get_beneficiarios_ids(db: Database, beneficiarios):
    ids = []
    for b in beneficiarios:
        desc = b['descripcion'].strip().upper()
        codigo = variables_.beneficiarios.get(desc, 0)
        id_ = buscar_o_insertar_catalogo(db, 'beneficiario', str(codigo), desc)
        if id_:
            ids.append(id_)
    return ids


def _get_instrumentos_ids(db: Database, instrumentos):
    ids = []
    for i in instrumentos:
        desc = i['descripcion'].strip().upper()
        codigo = variables_.instrumentos.get(desc, 0)
        id_ = buscar_o_insertar_catalogo(db, 'instrumento', str(codigo), desc)
        if id_:
            ids.append(id_)
    return ids


def _get_objetivos_ids(db: Database, objetivos):
    ids = []
    for o in objetivos:
        desc = o['descripcion']
        id_ = buscar_o_insertar_catalogo(db, 'objetivo', desc, desc)
        if id_:
            ids.append(id_)
    return ids
 

def get_id_organo_bdns(nivel3_desc: str):
    """
    Devuelve el ID numérico BDNS del órgano nivel3 consultando df_organos.
    El df_organos almacena IDs como string (ej. '12345'); se convierte a int.
    Devuelve None si no se encuentra o no es convertible a entero.

    Se llama SOLO desde buscar_organo_id(), que persiste el resultado en
    organos.id_bdns_nivel3. No la llames por convocatoria: para eso está la
    columna ya resuelta (resolverHistorial.py la lee con un JOIN).
    """
    if not nivel3_desc:
        return None
    df_organos = _get_df_organos()
    fila = df_organos[df_organos['organo'] == nivel3_desc]
    if len(fila) == 0:
        return None
    try:
        return int(fila['ID'].values[0])
    except (ValueError, TypeError):
        return None


def _update_documentos(db: Database, dat, conv_id):
    docs = json_documentos(dat.get('documentos', []), dat['codigoBDNS'])
    if not docs:
        return
    urls = [d['enlaceDocumento'] for d in docs]

    # 1. Borra SOLO los que ya no vienen de la API (sus chunks caen en cascada: correcto).
    db.query(
        "DELETE FROM documentos WHERE convocatoria_id = %s AND url <> ALL(%s)",
        data=(conv_id, urls)
    )
    # 2. Los que ya existen se dejan intactos: conservan su id y sus chunks.
    for doc in docs:
        db.query(
            """INSERT INTO documentos (convocatoria_id, nombre, url)
               VALUES (%s, %s, %s)
               ON CONFLICT (convocatoria_id, url) DO NOTHING""",
            data=(conv_id, doc['name_document'], doc['enlaceDocumento'])
        )


# ── Operaciones principales de BD ─────────────────────────────────────────────
def databaseInsert(dat, db: Database, tipe):
    conv_id = dat['id']

    # En modo Recientes solo insertar si no existe
    if tipe == 'Recientes':
        exists = db.query("SELECT 1 FROM convocatorias WHERE id = %s", data=(conv_id,))
        if exists:
            return

    fecha_recepcion = format_fecha(str(dat['fechaRecepcion']))
    fechas = resolver_fechas(dat, fecha_recepcion)

    organo = dat['organo']
    organo_id = buscar_organo_id(
        db, organo.get('nivel1'), organo.get('nivel2'), organo.get('nivel3')
    )

    finalidad_id = None
    if dat.get('descripcionFinalidad'):
        desc_f = dat['descripcionFinalidad'].strip().upper()
        id_f_num = variables_.inverted_finalidades.get(desc_f, 0)
        finalidad_id = buscar_o_insertar_catalogo(db, 'finalidad', str(id_f_num), desc_f)

    reglamento_id = None
    if dat.get('reglamento'):
        desc_r = dat['reglamento']['descripcion'].upper()
        id_r_num = variables_.inverted_reglamentos.get(desc_r, 0)
        reglamento_id = buscar_o_insertar_catalogo(db, 'reglamento', str(id_r_num), desc_r)


    sectores_ids      = _get_sectores_ids(db, dat.get('sectores', []))
    regiones_ids      = _get_regiones_ids(db, dat.get('regiones', []))
    beneficiarios_ids = _get_beneficiarios_ids(db, dat.get('tiposBeneficiarios', []))
    instrumentos_ids  = _get_instrumentos_ids(db, dat.get('instrumentos', []))
    objetivos_ids     = _get_objetivos_ids(db, dat.get('objetivos', []))

    upsert_sql = '''
        INSERT INTO convocatorias (
            id, codigo_bdns, numero_convocatoria, organo_id,
            descripcion, descripcion_leng, tipo_convocatoria, sede_electronica,
            presupuesto_total, fecha_recepcion,
            fecha_inicio_solicitud, fecha_fin_solicitud,
            text_inicio, text_fin, abierto_indefinido, estado_vigencia,
            bases_reguladoras, url_bases_reguladoras,
            publica_diario_oficial, ayuda_estado,
            finalidad_id, reglamento_id,
            sectores_ids, regiones_ids, beneficiarios_ids,
            instrumentos_ids, objetivos_ids,
            procesada, fuente
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            CAST(%s AS integer[]), CAST(%s AS integer[]),
            CAST(%s AS integer[]), CAST(%s AS integer[]),
            CAST(%s AS integer[]),
            FALSE, 'bdns'
        )
        ON CONFLICT (id) DO UPDATE SET
            codigo_bdns            = EXCLUDED.codigo_bdns,
            organo_id              = EXCLUDED.organo_id,
            descripcion            = EXCLUDED.descripcion,
            tipo_convocatoria      = EXCLUDED.tipo_convocatoria,
            presupuesto_total      = EXCLUDED.presupuesto_total,
            fecha_inicio_solicitud = EXCLUDED.fecha_inicio_solicitud,
            fecha_fin_solicitud    = EXCLUDED.fecha_fin_solicitud,
            text_inicio            = EXCLUDED.text_inicio,
            text_fin               = EXCLUDED.text_fin,
            abierto_indefinido     = EXCLUDED.abierto_indefinido,
            estado_vigencia        = EXCLUDED.estado_vigencia,
            sectores_ids           = EXCLUDED.sectores_ids,
            regiones_ids           = EXCLUDED.regiones_ids,
            beneficiarios_ids      = EXCLUDED.beneficiarios_ids,
            instrumentos_ids       = EXCLUDED.instrumentos_ids,
            objetivos_ids          = EXCLUDED.objetivos_ids,
            updated_at             = NOW()
    '''

    values = (
        conv_id,
        str(dat['codigoBDNS']),
        dat.get('numeroConvocatoria'),
        organo_id,
        dat['descripcion'],
        dat.get('descripcionLeng'),
        dat.get('tipoConvocatoria'),
        dat.get('sedeElectronica'),
        dat.get('presupuestoTotal'),
        fecha_recepcion,
        fechas['fechaInicio'],
        fechas['fechaFin'],
        fechas['textInicioOriginal'],
        fechas['textFinOriginal'],
        bool(dat.get('abierto', False)),
        fechas['estadoVigencia'],
        dat.get('descripcionBasesReguladoras'),
        dat.get('urlBasesReguladoras'),
        bool(dat.get('sePublicaDiarioOficial', False)),
        dat.get('ayudaEstado'),
        finalidad_id,
        reglamento_id,
        _to_pg_int_array(sectores_ids),
        _to_pg_int_array(regiones_ids),
        _to_pg_int_array(beneficiarios_ids),
        _to_pg_int_array(instrumentos_ids),
        _to_pg_int_array(objetivos_ids),
    )

    db.query(upsert_sql, data=values)
    _update_documentos(db, dat, conv_id)
    # id_bdns_nivel3 ya se resolvió (y persistió) dentro de buscar_organo_id()
    # más arriba; calcular_y_guardar() no necesita tocar BDNS para V9.
    _scorer.calcular_y_guardar(dat, db)
    _embedder.calcular_y_guardar(dat, db)


def conect_database() -> Database:
    return conectar()


def select_convocatorias(db: Database):
    return db.query(
        "SELECT id, fecha_fin_solicitud, text_fin FROM convocatorias "
        "WHERE estado_vigencia = 'Abierta' AND abierto_indefinido = FALSE"
    )


def select_convocatorias_abiertas(db: Database):
    return db.query(
        "SELECT codigo_bdns, id FROM convocatorias "
        "WHERE estado_vigencia IN ('Abierta', 'Pendiente', 'Indefinida')"
    )


def prueba(db: Database):
    return db.query("SELECT codigo_bdns FROM convocatorias WHERE codigo_bdns = '867073'")


def update_date(db: Database, id_convo):
    db.query(
        "UPDATE convocatorias SET estado_vigencia = 'Cerrada' WHERE id = %s",
        data=(id_convo,)
    )


def consultar_convotaria(cog_convo, db_, tipe_):
    params = {'numConv': cog_convo}

    for intento in range(5):
        try:
            response = requests.get(url=f'{URLAPI}convocatorias', params=params)
        except Exception:
            continue

        if response.status_code == 200:
            dat = response.json()
            if dat['tipoConvocatoria'] not in ['Concurrencia competitiva - canónica', 'Concesión directa - canónica']:
                return False
            databaseInsert(dat=dat, db=db_, tipe=tipe_)
            return True
        elif response.status_code == 204:
            return False

        return False
