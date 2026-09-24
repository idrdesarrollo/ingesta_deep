"""Marca como 'Cerrada' las convocatorias abiertas cuya fecha de fin ya pasó.

Uso: python updateConvoca.py
"""
from keywords import datBBDD
from datetime import date

# fecha_fin_solicitud es DATE en BD (llega como datetime.date): comparar
# siempre date con date; date < datetime lanza TypeError.
solo_fecha = date.today()


def updateConvocatorias():
    db = datBBDD.conect_database()
    try:
        for row in datBBDD.select_convocatorias(db):
            isAbierta = validar_fecha(row['fecha_fin_solicitud'], row['text_fin'])
            if isAbierta == 'False':
                datBBDD.update_date(db, row['id'])
    finally:
        db.close()


def validar_fecha(fechaFin, textFin):
    isAbierta = 'True'
    #------------------------------------------------------------
    #Validar fechas para revisar si esta abierta o no
    if fechaFin != None:
        if fechaFin < solo_fecha:
            # La convocatoria todavia esta abierta
            isAbierta = 'False'
    else:
        if textFin != None:
            validate, date_ = datBBDD.is_date(textFin)
            if validate:
                if date_.date() < solo_fecha:
                    isAbierta = 'False'
    
    return isAbierta


if __name__ == '__main__':
    updateConvocatorias()
