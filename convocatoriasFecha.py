"""Trae las convocatorias recibidas en un rango de fechas.

Antes recibía 'desde'/'hasta' por work item de Robocorp; ahora por argumentos.
Uso: python convocatoriasFecha.py --desde 01/09/2026 --hasta 15/09/2026
"""
import argparse

from keywords import listar_convocatorias
from keywords import datBBDD


def convocatoriasFechas(desde: str, hasta: str):
    params = {
        'order': 'fechaRecepcion',
        'direccion': 'desc',
        'vpd': 'GE',
        'fechaDesde' : desde,
        'fechaHasta' : hasta,
        'pageSize' : 10000,
    }

    ejecutar_busqueda(parametro=params)


def ejecutar_busqueda(parametro):
    db = datBBDD.conect_database()
    try:
        convocatorias = listar_convocatorias.traer_convocatorias(parametro)
        for convocatoria in convocatorias:
            datBBDD.consultar_convotaria(str(convocatoria), db, 'Fechas')
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Trae las convocatorias recibidas en un rango de fechas.')
    parser.add_argument('--desde', required=True, help='Fecha inicial, formato dd/mm/aaaa')
    parser.add_argument('--hasta', required=True, help='Fecha final, formato dd/mm/aaaa')
    args = parser.parse_args()
    convocatoriasFechas(args.desde, args.hasta)
