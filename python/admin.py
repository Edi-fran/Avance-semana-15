# python/admin.py
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from werkzeug.security import generate_password_hash
from mysql.connector import Error
from python.conexion import get_conn
from python.authz import roles_required

bp = Blueprint("admin", __name__, url_prefix="/admin", template_folder="../templates")

@bp.get("/")
@login_required
@roles_required("administrador")
def panel():
    # atajo: manda al listado de usuarios
    return redirect(url_for("admin.usuarios"))

@bp.get("/usuarios")
@login_required
@roles_required("administrador")
def usuarios():
    q = (request.args.get("q") or "").strip()
    cn = get_conn(); cur = cn.cursor()
    sql = """
        SELECT u.id_usuario, u.usuario_login, u.email, u.activo,
               COALESCE(GROUP_CONCAT(r.nombre ORDER BY r.nombre SEPARATOR ', '), '')
        FROM usuario u
        LEFT JOIN usuario_rol ur ON ur.id_usuario = u.id_usuario
        LEFT JOIN rol r ON r.id_rol = ur.id_rol
        {where}
        GROUP BY u.id_usuario, u.usuario_login, u.email, u.activo
        ORDER BY u.id_usuario DESC
    """
    if q:
        cur.execute(sql.format(where="WHERE u.usuario_login LIKE %s OR u.email LIKE %s"),
                    (f"%{q}%", f"%{q}%"))
    else:
        cur.execute(sql.format(where=""))
    rows = cur.fetchall()
    cur.close(); cn.close()
    return render_template("admin/usuarios.html", rows=rows, q=q)

@bp.get("/usuarios/<int:id_usuario>/editar")
@login_required
@roles_required("administrador")
def editar_usuario(id_usuario: int):
    cn = get_conn(); cur = cn.cursor()
    cur.execute("""SELECT id_usuario, nombre_completo, usuario_login, email, activo, mfa_habilitado
                   FROM usuario WHERE id_usuario=%s""", (id_usuario,))
    u = cur.fetchone()
    if not u:
        cur.close(); cn.close()
        flash("Usuario no encontrado.", "warning")
        return redirect(url_for("admin.usuarios"))

    cur.execute("SELECT id_rol, nombre FROM rol WHERE activo=1 ORDER BY nombre")
    roles = cur.fetchall()

    cur.execute("""SELECT r.id_rol
                   FROM usuario_rol ur JOIN rol r ON r.id_rol=ur.id_rol
                   WHERE ur.id_usuario=%s""", (id_usuario,))
    roles_usuario = {row[0] for row in cur.fetchall()}

    cur.close(); cn.close()
    return render_template("admin/usuario_editar.html",
                           u=u, roles=roles, roles_usuario=roles_usuario)

@bp.post("/usuarios/<int:id_usuario>/actualizar")
@login_required
@roles_required("administrador")
def actualizar_usuario(id_usuario: int):
    nombre = (request.form.get("nombre_completo") or "").strip()
    login  = (request.form.get("usuario_login") or "").strip()
    email  = (request.form.get("email") or "").strip().lower()
    activo = 1 if request.form.get("activo") == "1" else 0
    mfa    = 1 if request.form.get("mfa_habilitado") == "1" else 0
    nuevo_pwd = request.form.get("password") or ""
    # Combo único: 'administrador' o 'facturador'
    rol_seleccionado = request.form.get("rol")  # puede venir vacío

    cn = get_conn(); cur = cn.cursor()
    try:
        # Unicidad login/email (excluyendo al propio usuario)
        cur.execute("SELECT 1 FROM usuario WHERE usuario_login=%s AND id_usuario<>%s", (login, id_usuario))
        if cur.fetchone():
            flash("El nombre de usuario ya existe.", "warning")
            return redirect(url_for("admin.editar_usuario", id_usuario=id_usuario))

        cur.execute("SELECT 1 FROM usuario WHERE email=%s AND id_usuario<>%s", (email, id_usuario))
        if cur.fetchone():
            flash("El email ya está registrado.", "warning")
            return redirect(url_for("admin.editar_usuario", id_usuario=id_usuario))

        # Update de datos básicos + password si cambió
        if nuevo_pwd:
            hp = generate_password_hash(nuevo_pwd)
            cur.execute("""UPDATE usuario
                           SET nombre_completo=%s, usuario_login=%s, email=%s,
                               activo=%s, mfa_habilitado=%s, hash_password=%s
                           WHERE id_usuario=%s""",
                        (nombre, login, email, activo, mfa, hp, id_usuario))
        else:
            cur.execute("""UPDATE usuario
                           SET nombre_completo=%s, usuario_login=%s, email=%s,
                               activo=%s, mfa_habilitado=%s
                           WHERE id_usuario=%s""",
                        (nombre, login, email, activo, mfa, id_usuario))

        # Rol (único): limpia y asigna uno si se envió
        if rol_seleccionado:
            cur.execute("SELECT id_rol FROM rol WHERE nombre=%s AND activo=1", (rol_seleccionado,))
            r = cur.fetchone()
            if r:
                cur.execute("DELETE FROM usuario_rol WHERE id_usuario=%s", (id_usuario,))
                cur.execute("INSERT INTO usuario_rol (id_usuario, id_rol) VALUES (%s, %s)", (id_usuario, r[0]))

        cn.commit()
        flash("Usuario actualizado correctamente.", "success")
        return redirect(url_for("admin.usuarios"))

    except Error as e:
        cn.rollback()
        flash(f"No se pudo actualizar: {e}", "danger")
        return redirect(url_for("admin.editar_usuario", id_usuario=id_usuario))
    finally:
        try:
            cur.close(); cn.close()
        except Exception:
            pass

