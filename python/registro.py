# python/registro.py
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash
from mysql.connector import Error, InterfaceError, DatabaseError
from python.conexion import get_conn   # conexión central a MariaDB

bp = Blueprint("registro", __name__, url_prefix="/registro")

# Mapa de códigos del combo -> nombre real en la tabla rol
ROL_MAP = {"adm": "administrador", "fact": "facturador"}

@bp.get("/")
def form():
    return render_template("form_registro.html")

@bp.post("/")
def crear():
    nombre   = (request.form.get("nombre_completo") or "").strip()
    usuario  = (request.form.get("usuario_login") or "").strip()
    email    = (request.form.get("email") or "").strip().lower()
    pwd      = request.form.get("password", "")
    pwd2     = request.form.get("password2", "")
    activo   = "1" if (request.form.get("activo", "1") == "1") else "0"
    mfa      = "1" if (request.form.get("mfa_habilitado", "0") == "1") else "0"
    rol_code = request.form.get("rol_code")  # 'adm' o 'fact'

    # ---- Validaciones
    if not all([nombre, usuario, email, pwd, pwd2, rol_code]):
        flash("Completa todos los campos y selecciona un rol.", "warning")
        return redirect(url_for("registro.form"))
    if rol_code not in ROL_MAP:
        flash("Selecciona un rol válido (Administrador o Facturador).", "warning")
        return redirect(url_for("registro.form"))
    if pwd != pwd2:
        flash("Las contraseñas no coinciden.", "danger")
        return redirect(url_for("registro.form"))
    if len(pwd) < 8:
        flash("La contraseña debe tener al menos 8 caracteres.", "warning")
        return redirect(url_for("registro.form"))

    hash_password = generate_password_hash(pwd)

    # ---- Conexión central
    try:
        cn = get_conn()
    except (RuntimeError, InterfaceError, DatabaseError) as err:
        flash(str(err), "danger")
        return redirect(url_for("registro.form"))

    cur = cn.cursor()
    try:
        # Unicidad
        cur.execute("SELECT 1 FROM usuario WHERE usuario_login=%s", (usuario,))
        if cur.fetchone():
            flash("El nombre de usuario ya existe.", "warning")
            cn.rollback()
            return redirect(url_for("registro.form"))

        cur.execute("SELECT 1 FROM usuario WHERE email=%s", (email,))
        if cur.fetchone():
            flash("El email ya está registrado.", "warning")
            cn.rollback()
            return redirect(url_for("registro.form"))

        # Insert usuario
        cur.execute(
            """
            INSERT INTO usuario
              (nombre_completo, usuario_login, email, hash_password, activo, mfa_habilitado)
            VALUES
              (%s, %s, %s, %s, %s, %s)
            """,
            (nombre, usuario, email, hash_password, activo, mfa),
        )
        cn.commit()

        # Obtener id del nuevo usuario (fallback por si lastrowid no viene)
        id_usuario = cur.lastrowid
        if not id_usuario:
            cur.execute("SELECT id_usuario FROM usuario WHERE usuario_login=%s", (usuario,))
            row = cur.fetchone()
            id_usuario = row[0] if row else None

        # Asignar rol elegido
        rol_nombre = ROL_MAP[rol_code]  # administrador / facturador
        cur.execute("SELECT id_rol FROM rol WHERE LOWER(nombre)=LOWER(%s) AND activo=1", (rol_nombre,))
        r = cur.fetchone()
        if not r:
            # Usuario creado pero rol inexistente/inactivo → advertimos
            flash(f"Advertencia: el rol '{rol_nombre}' no existe o está inactivo.", "warning")
            return redirect(url_for("auth.login_form"))

        id_rol = r[0]
        cur.execute(
            "INSERT IGNORE INTO usuario_rol (id_usuario, id_rol) VALUES (%s, %s)",
            (id_usuario, id_rol),
        )
        cn.commit()

        flash("Cuenta creada correctamente. Ahora inicia sesión.", "success")
        return redirect(url_for("auth.login_form"))

    except Error as e:
        cn.rollback()
        msg = str(e).lower()
        if "usuario_login" in msg:
            flash("El nombre de usuario ya existe.", "warning")
        elif "email" in msg:
            flash("El email ya está registrado.", "warning")
        else:
            flash("No se pudo crear la cuenta.", "danger")
        return redirect(url_for("registro.form"))
    finally:
        try:
            cur.close()
            cn.close()
        except Exception:
            pass


