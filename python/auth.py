# python/auth.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, UserMixin, current_user
from werkzeug.security import check_password_hash
import os, requests, time
from urllib.parse import urlparse, urljoin
from mysql.connector import InterfaceError, DatabaseError
from python.conexion import get_conn  # ✅ conexión central a MariaDB

bp = Blueprint("auth", __name__)

# === Config de intentos ===
MAX_ATTEMPTS = 3
LOCK_SECONDS = 5 * 60  # 5 minutos

class Usuario(UserMixin):
    def __init__(self, id_usuario, usuario_login, hash_password):
        self.id = id_usuario
        self.usuario_login = usuario_login
        self.hash_password = hash_password

# === Helpers ===
def _is_locked():
    lock_until = session.get("lock_until", 0)
    now = int(time.time())
    if lock_until and now < lock_until:
        return True
    if lock_until and now >= lock_until:
        session.pop("lock_until", None)
        session.pop("login_attempts", None)
    return False

def _remaining_minutes():
    lock_until = session.get("lock_until", 0)
    if not lock_until:
        return 0
    rem = lock_until - int(time.time())
    return max(1, rem // 60) if rem > 0 else 0

def _bump_attempts():
    attempts = int(session.get("login_attempts", 0)) + 1
    session["login_attempts"] = attempts
    if attempts >= MAX_ATTEMPTS:
        session["lock_until"] = int(time.time()) + LOCK_SECONDS

def _is_safe_next(target):
    if not target:
        return False
    host_url = request.host_url
    test_url = urljoin(host_url, target)
    return (
        urlparse(test_url).scheme in ("http", "https")
        and urlparse(test_url).netloc == urlparse(host_url).netloc
    )

# === Rutas ===
@bp.get("/login")
def login_form():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if _is_locked():
        flash(f"Demasiados intentos fallidos. Inténtalo en ~{_remaining_minutes()} min.", "warning")

    # Guarda 'next' (si venías de una ruta protegida)
    next_url = request.args.get("next")
    if _is_safe_next(next_url):
        session["next_url"] = next_url

    site_key = os.getenv("RECAPTCHA_SITE_KEY", "")
    return render_template("form_usuario.html", recaptcha_site_key=site_key)

@bp.post("/login")
def login_post():
    if _is_locked():
        flash("Acceso temporalmente bloqueado.", "warning")
        return redirect(url_for("auth.login_form"))

    u = (request.form.get("usuario") or request.form.get("usuario_login") or "").strip()
    p = request.form.get("password", "")

    if not u or not p:
        _bump_attempts()
        flash("Ingrese sus credenciales (usuario y contraseña).", "warning")
        return redirect(url_for("auth.login_form"))

    # --- reCAPTCHA ---
    token = request.form.get("g-recaptcha-response", "")
    secret = os.getenv("RECAPTCHA_SECRET", "")
    if not token or not secret:
        _bump_attempts()
        flash("Falta verificar reCAPTCHA.", "warning")
        return redirect(url_for("auth.login_form"))
    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": secret, "response": token},
            timeout=6,
        ).json()
    except requests.RequestException:
        _bump_attempts()
        flash("No se pudo verificar reCAPTCHA (red). Intenta de nuevo.", "danger")
        return redirect(url_for("auth.login_form"))

    if not resp.get("success"):
        _bump_attempts()
        flash("reCAPTCHA inválido.", "danger")
        return redirect(url_for("auth.login_form"))

    # --- Autenticación en BD (MariaDB) ---
    try:
        cn = get_conn(); cur = cn.cursor()
        cur.execute(
            """SELECT id_usuario, usuario_login, hash_password
               FROM usuario
               WHERE usuario_login=%s AND activo=1""",
            (u,),
        )
        row = cur.fetchone()
        cur.close(); cn.close()
    except (InterfaceError, DatabaseError, RuntimeError):
        _bump_attempts()
        flash("No se puede conectar a MariaDB. Verifica que el servicio esté en ejecución y .env (host/puerto/usuario/clave) sea correcto.", "danger")
        return redirect(url_for("auth.login_form"))

    if row and check_password_hash(row[2], p):
        # Reset intentos y login
        session.pop("login_attempts", None)
        session.pop("lock_until", None)
        login_user(Usuario(row[0], row[1], row[2]))

        # === Registrar sesión en tabla `sesion` ===
        try:
            cn = get_conn(); cur = cn.cursor()
            cur.execute("""
                INSERT INTO sesion (id_usuario, inicio, ip, user_agent, estado)
                VALUES (%s, NOW(), %s, %s, 'activa')
            """, (
                int(current_user.id),
                request.headers.get("X-Forwarded-For", request.remote_addr),
                (request.user_agent.string or "")[:255]
            ))
            cn.commit()
            session["sesion_id"] = cur.lastrowid
            cur.close(); cn.close()
        except Exception:
            # No interrumpir si falla el registro de sesión
            session.pop("sesion_id", None)

        flash("Bienvenido.", "success")
        next_url = session.pop("next_url", None)
        return redirect(next_url if _is_safe_next(next_url) else url_for("index"))

    _bump_attempts()
    flash("Usuario o contraseña incorrectos.", "danger")
    return redirect(url_for("auth.login_form"))

@bp.post("/logout")
@login_required
def logout():
    # Cerrar la sesión en BD si la tenemos
    try:
        sid = session.get("sesion_id")
        if sid:
            cn = get_conn(); cur = cn.cursor()
            cur.execute("UPDATE sesion SET fin=NOW(), estado='cerrada' WHERE id_sesion=%s", (sid,))
            cn.commit()
            cur.close(); cn.close()
    except Exception:
        pass
    session.pop("sesion_id", None)

    logout_user()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("auth.login_form"))
