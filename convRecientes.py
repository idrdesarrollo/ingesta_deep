"""Ejecución diaria: convocatorias recibidas entre ayer y hoy.

Uso: python convRecientes.py
"""
from datetime import datetime, timedelta
from keywords import listar_convocatorias
from keywords import datBBDD


def convRecientes():
    fecha_reciente = datetime.now()
    fecha_anterior = fecha_reciente+timedelta(days=-1)
    fecha_reciente = datetime.strftime(fecha_reciente, '%d/%m/%Y')
    fecha_anterior = datetime.strftime(fecha_anterior, '%d/%m/%Y')

    params = {
        'order': 'fechaRecepcion',
        'direccion': 'desc',
        'vpd': 'GE',
        'fechaDesde' : f'{fecha_anterior}',
        'fechaHasta' : f'{fecha_reciente}',
        'pageSize' : 10000,
    }

    ejecutar_busqueda(parametro=params)


def ejecutar_busqueda(parametro):
    db = datBBDD.conect_database()
    try:
        convocatorias = listar_convocatorias.traer_convocatorias(parametro)
        for convocatoria in convocatorias:
            datBBDD.consultar_convotaria(str(convocatoria), db, 'Recientes')
    finally:
        db.close()


if __name__ == '__main__':
    convRecientes()
