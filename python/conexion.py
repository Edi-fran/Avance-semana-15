# python/conexion.py
import os
from contextlib import contextmanager
import mysql.connector

# ===== Configuración (usa .env si existe; si no, defaults que tú pediste) =====
HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
PORT = int(os.getenv("MYSQL_PORT", "6000"))           # por defecto 6000
USER = os.getenv("MYSQL_USER", "root")
PASSWORD = os.getenv("MYSQL_PASSWORD", "501914")      # por defecto 501914
DATABASE = os.getenv("MYSQL_DATABASE", "repaircell_db")

CFG = dict(
    host=HOST,
    port=PORT,
    user=USER,
    password=PASSWORD,
    database=DATABASE,
    autocommit=False,
)

def get_conn():
    """Crea una conexión nueva (se abre al llamar y la cierras luego)."""
    return mysql.connector.connect(**CFG)

@contextmanager
def connect(dict_rows: bool = False):
    """
    Context manager que abre conexión y cursor, y los cierra solo.
    dict_rows=True => filas como dict (columnas por nombre).
    Uso:
        with connect(True) as (cn, cur):
            cur.execute("SELECT ...", params)
            rows = cur.fetchall()
    """
    cn = get_conn()
    cur = cn.cursor(dictionary=dict_rows)
    try:
        yield cn, cur
        cn.commit()
    except Exception:
        cn.rollback()
        raise
    finally:
        try:
            cur.close()
        finally:
            cn.close()

def query(sql: str, params: tuple = (), *, dict_rows: bool = True):
    """SELECT -> lista de filas (por defecto dicts). Abre/cierra por ti."""
    with connect(dict_rows) as (_, cur):
        cur.execute(sql, params)
        return cur.fetchall()

def query_one(sql: str, params: tuple = (), *, dict_rows: bool = True):
    """SELECT -> una fila o None. Abre/cierra por ti."""
    with connect(dict_rows) as (_, cur):
        cur.execute(sql, params)
        return cur.fetchone()

def execute(sql: str, params: tuple = ()):
    """
    INSERT/UPDATE/DELETE -> (rowcount, lastrowid). Abre/cierra por ti.
    """
    with connect(False) as (cn, cur):
        cur.execute(sql, params)
        try:
            last_id = cur.lastrowid
        except Exception:
            last_id = None
        return cur.rowcount, last_id

def executemany(sql: str, seq_params: list[tuple]):
    """Múltiples INSERT/UPDATE/DELETE. Abre/cierra por ti."""
    with connect(False) as (cn, cur):
        cur.executemany(sql, seq_params)
        return cur.rowcount

def ping() -> bool:
    """Pequeña prueba de vida de la DB."""
    try:
        with connect() as (_, cur):
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        return False

