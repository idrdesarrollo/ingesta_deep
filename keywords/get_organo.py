import pandas as pd
import requests
import json


def main():
    URLAPI = 'https://www.infosubvenciones.es/bdnstrans/api'

    df_organos = []

    lst_tipe = ['C', 'A', 'L', 'O']
    
    dict_tipe = {
        'L' : 1,
        'C' : 2,
        'A' : 3,
        'O' : 4
    }


    for tipe in lst_tipe:
        params = {
            'vpd' : 'GE',
            'idAdmon' : f'{tipe}'
        }

        response = requests.get(url=f'{URLAPI}/organos', params=params)
        organo = response.json()

        match tipe:
            case 'L':
                for item in organo:
                    for i in item['children']:
                        dat = {
                            'ID' : i['id'],
                            'organo' : i['descripcion'],
                            'tipe' : 'Entidad Local'
                        }
                        df_organos.append(dat)
                        for children_ in i['children']:
                            dat_children = {
                                'ID' : children_['id'],
                                'organo' : children_['descripcion'],
                                'tipe' : 'Entidad Local'
                            }
                            df_organos.append(dat_children)
            case 'C':
                for item in organo:
                    dat = {
                        'ID' : item['id'],
                        'organo' : item['descripcion'],
                        'tipe' : 'Administracion del Estado'
                    }
                    df_organos.append(dat)

                    for children in item['children']:
                        dat_children = {
                            'ID' : children['id'],
                            'organo' : children['descripcion'],
                            'tipe' : 'Administracion del Estado'
                        }
                        df_organos.append(dat_children)
                
                df_organos.append({'ID': '1', 'organo': 'ESTADO', 'tipe': 'Administracion del Estado'})
                
            case 'A':
                for item in organo:
                    dat = {
                        'ID' : item['id'],
                        'organo' : item['descripcion'],
                        'tipe' : 'Comunidad Autonoma'
                    }
                    df_organos.append(dat)
                    for children in item['children']:
                        dat_children = {
                            'ID' : children['id'],
                            'organo' : children['descripcion'],
                            'tipe' : 'Comunidad Autonoma'
                        }
                        df_organos.append(dat_children)
            case 'O':
                for item in organo:
                    dat = {
                        'ID' : item['id'],
                        'organo' : item['descripcion'],
                        'tipe' : 'Otros'
                    }
                    df_organos.append(dat)
                
                df_organos.append({'ID': '1', 'organo': 'OTROS', 'tipe': 'Otros'})
            case _:
                print("Unknown Class")

    return pd.DataFrame(df_organos)