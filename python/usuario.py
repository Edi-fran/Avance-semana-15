# python/registro.py
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash
from mysql.connector import Error
from python.conexion import get_conn  # ✅ conexión central MariaDB

bp = Blueprint("registro", __name__, url_prefix="/registro")

@bp.get("/")
def form():
    # Usa templates/form_registro.html (el que ya estilaste con Bootstrap 5)
    return render_template("form_registro.html")

@bp.post("/")
def crear():
    nombre   = (request.form.get("nombre_completo") or "").strip()
    usuario  = (request.form.get("usuario_login") or "").strip()
    email    = (request.form.get("email") or "").strip().lower()
    pwd      = request.form.get("password", "")
    pwd2     = request.form.get("password2", "")
    # estos pueden no venir en el form; definimos valores por defecto
    activo   = "1" if (request.form.get("activo", "1") == "1") else "0"
    mfa      = "1" if (request.form.get("mfa_habilitado", "0") == "1") else "0"

    # ---- Validaciones básicas
    if not all([nombre, usuario, email, pwd, pwd2]):
        flash("Completa todos los campos.", "warning")
        return redirect(url_for("registro.form"))
    if pwd != pwd2:
        flash("Las contraseñas no coinciden.", "danger")
        return redirect(url_for("registro.form"))
    if len(pwd) < 8:
        flash("La contraseña debe tener al menos 8 caracteres.", "warning")
        return redirect(url_for("registro.form"))

    hash_password = generate_password_hash(pwd)

    try:
        cn = get_conn()
    except Exception as err:
        flash(f"No puedo conectar a la base de datos: {err}", "danger")
        return redirect(url_for("registro.form"))

    cur = cn.cursor()
    try:
        # ---- Unicidad (mensajes claros)
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

        # ---- Insert usuario
        cur.execute("""
            INSERT INTO usuario
                (nombre_completo, usuario_login, email, hash_password, activo, mfa_habilitado)
            VALUES
                (%s, %s, %s, %s, %s, %s)
        """, (nombre, usuario, email, hash_password, activo, mfa))
        cn.commit()

        # ---- Rol por defecto: facturador (si existe)
        try:
            cur.execute("SELECT id_usuario FROM usuario WHERE usuario_login=%s", (usuario,))
            id_usuario = cur.fetchone()[0]
            cur.execute("SELECT id_rol FROM rol WHERE nombre=%s", ("facturador",))
            r = cur.fetchone()
            if r:
                cur.execute(
                    "INSERT IGNORE INTO usuario_rol (id_usuario, id_rol) VALUES (%s, %s)",
                    (id_usuario, r[0])
                )
                cn.commit()
        except Error:
            # si falla la asignación de rol, no rompemos el alta
            cn.rollback()

        flash("Cuenta creada correctamente. Ahora puedes iniciar sesión.", "success")
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

