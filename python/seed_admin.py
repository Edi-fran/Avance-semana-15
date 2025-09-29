# python/seed_admin.py
from werkzeug.security import generate_password_hash
from mysql.connector import Error
from python.conexion import get_conn

def _ensure_role(cur, nombre: str) -> int:
    cur.execute("SELECT id_rol FROM rol WHERE LOWER(nombre)=LOWER(%s)", (nombre,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO rol (nombre, activo) VALUES (%s, 1)", (nombre,))
    return cur.lastrowid

def _ensure_user_admin(cur, login: str, email: str, plain_pwd: str) -> int:
    cur.execute("SELECT id_usuario FROM usuario WHERE usuario_login=%s", (login,))
    row = cur.fetchone()
    if row:
        # Asegurar activo=1 (sin tocar hash si ya existe)
        cur.execute("UPDATE usuario SET activo=1 WHERE id_usuario=%s", (row[0],))
        return row[0]

    hash_pwd = generate_password_hash(plain_pwd)
    cur.execute(
        """
        INSERT INTO usuario (nombre_completo, usuario_login, email, hash_password, activo, mfa_habilitado)
        VALUES (%s, %s, %s, %s, 1, 0)
        """,
        ("Administrador", login, email, hash_pwd),
    )
    return cur.lastrowid

def _link_role(cur, id_usuario: int, id_rol: int) -> None:
    cur.execute(
        "INSERT IGNORE INTO usuario_rol (id_usuario, id_rol) VALUES (%s, %s)",
        (id_usuario, id_rol),
    )

def bootstrap_admin(login: str, email: str, plain_pwd: str) -> None:
    """
    Idempotente: si el usuario/rol ya existen, no duplica.
    Se llama al iniciar la app.
    """
    cn = get_conn()
    cur = cn.cursor()
    try:
        # Asegura roles base (por si no existen)
        id_admin = _ensure_role(cur, "administrador")
        _ensure_role(cur, "facturador")

        # Crea/activa usuario admin por defecto
        id_usuario = _ensure_user_admin(cur, login, email, plain_pwd)

        # Vincula usuario -> rol administrador (si aún no existe)
        _link_role(cur, id_usuario, id_admin)

        cn.commit()
    except Error:
        cn.rollback()
        # No re-raise para no tumbar la app si la DB no está disponible al arranque
    finally:
        try:
            cur.close(); cn.close()
        except Exception:
            pass
