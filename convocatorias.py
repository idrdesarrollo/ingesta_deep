"""Carga histórica completa de convocatorias.

Uso: python convocatorias.py
"""
from keywords import listar_convocatorias
from keywords import datBBDD


def convocatoriasAll():
    """Obtener todas las convocatorias"""
    params = {
        'order': 'fechaRecepcion',
        'direccion': 'desc',
        'vpd': 'GE',
        'pageSize' : 10000,
    }

    ejecutar_busqueda(parametro=params)


def ejecutar_busqueda(parametro):
    db = datBBDD.conect_database()
    try:
        convocatorias = listar_convocatorias.traer_convocatorias(parametro)
        for convocatoria in convocatorias:
            datBBDD.consultar_convotaria(str(convocatoria), db, 'Todas')
            break
    finally:
        db.close()


if __name__ == '__main__':
    convocatoriasAll()
