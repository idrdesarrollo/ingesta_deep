import requests

URLAPI = 'https://www.infosubvenciones.es/bdnstrans/api'

def traer_convocatorias(params):
    lista = [x for x in range(1, 88)]
    params['regiones'] = lista 
    response = requests.get(f'{URLAPI}/convocatorias/busqueda', params=params)
    # print(response._content)
    if response.status_code == 200:
        data = response.json()
        # Navegar por todas las paginas
        pages = data["totalPages"]
        pages += 1
        numPages = [x for x in range(0, pages)]
        list_convotorias = []
        for Page_ in numPages:
            params['page'] = Page_
            response = requests.get(f'{URLAPI}/convocatorias/busqueda', params=params)
            if response.status_code == 200:
                data = response.json()
                content = data["content"]
                for item in content:
                    list_convotorias.append(item["numeroConvocatoria"])
        print("Exitoso")
    else:
        print(f"Error en la solicitud: {response.status_code}")
    return list_convotorias