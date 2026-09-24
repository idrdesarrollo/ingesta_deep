"""Conexión a PostgreSQL con psycopg2 (sustituye a RPA.Database).

Mantiene la semántica que tenía RPA.Database.query():
- Cada sentencia se confirma por separado (autocommit): un fallo en el
  scoring o el embedding de una convocatoria no deja la conexión abortada
  ni deshace el UPSERT ya hecho.
- Las sentencias que devuelven filas (SELECT, ... RETURNING) devuelven una
  lista de filas; el resto devuelve None.
- Cada fila admite acceso por posición (fila[0], desempaquetado) y por
  nombre de columna (fila['id']) -psycopg2.extras.DictRow-.

Credenciales por variables de entorno (DB_NAME, DB_USER, DB_PASSWORD,
DB_HOST, DB_PORT), que pueden venir de un fichero .env en la raíz del
proyecto (ver .env.example). Nunca en el código.
"""

import os

import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

load_dotenv()


class Database:
    def __init__(self, conn):
        self._conn = conn

    def query(self, statement: str, data=None):
        with self._conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(statement, data)
            if cur.description is None:
                return None
            return cur.fetchall()

    def close(self) -> None:
        self._conn.close()


def conectar() -> Database:
    conn = psycopg2.connect(
        dbname=os.environ.get("DB_NAME", "oceano_azul"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD"),
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
    )
    conn.autocommit = True
    return Database(conn)
