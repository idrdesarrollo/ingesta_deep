"""Refresca desde BDNS las convocatorias abiertas/pendientes/indefinidas.

Uso: python updateInfo.py
"""
from keywords import datBBDD


def updateInfo():
    db = datBBDD.conect_database()
    try:
        for row in datBBDD.select_convocatorias_abiertas(db):
            status = datBBDD.consultar_convotaria(str(row['codigo_bdns']), db, 'UPDATE')
            if not status:
                datBBDD.update_date(db, row['id'])
    finally:
        db.close()


if __name__ == '__main__':
    updateInfo()
