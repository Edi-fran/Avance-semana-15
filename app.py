# app.py
from __future__ import annotations
import os
from flask import Flask, render_template, redirect, url_for, g
from flask_login import LoginManager, login_required, current_user
from dotenv import load_dotenv
from mysql.connector import InterfaceError, DatabaseError

# Core helpers
from python.conexion import get_conn                 # conexión central MariaDB
from python.authz import user_roles, has_role        # roles y helper para plantillas
from python.seed_admin import bootstrap_admin        # siembra admin al arrancar

load_dotenv()


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev"),
        SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "Lax"),
        SESSION_COOKIE_SECURE=bool(int(os.getenv("SESSION_COOKIE_SECURE", "0"))),
    )

    # ===== Datos del negocio / IVA disponibles en templates =====
    for k in ["NEGOCIO_NOMBRE", "NEGOCIO_RUC", "NEGOCIO_DIR", "NEGOCIO_TEL", "IVA_PORCENTAJE"]:
        app.config[k] = os.getenv(k, app.config.get(k))
    # Exponer config y helper de rol en Jinja
    app.jinja_env.globals.update(
        config=app.config,
        has_role=has_role,  # uso en plantillas: {% if has_role('administrador') %} ... {% endif %}
    )

    # ===== Flask-Login =====
    login_manager = LoginManager()
    login_manager.login_view = "auth.login_form"
    login_manager.login_message_category = "warning"
    login_manager.init_app(app)

    from python.auth import Usuario  # clase UserMixin

    @login_manager.user_loader
    def load_user(user_id: str):
        """Carga el usuario desde la BD para Flask-Login."""
        try:
            cn = get_conn(); cur = cn.cursor()
            cur.execute(
                """SELECT id_usuario, usuario_login, hash_password
                   FROM usuario
                   WHERE id_usuario=%s AND activo=1""",
                (user_id,),
            )
            row = cur.fetchone()
            cur.close(); cn.close()
        except (InterfaceError, DatabaseError):
            return None
        return Usuario(row[0], row[1], row[2]) if row else None

    # ===== Seed del admin por defecto (idempotente) =====
    # Crea/asegura un usuario admin si no existe. No rompe el arranque si la DB aún no responde.
    try:
        bootstrap_admin(
            login=os.getenv("ADMIN_BOOT_USER", "admin"),
            email=os.getenv("ADMIN_BOOT_EMAIL", "admin@example.com"),
            plain_pwd=os.getenv("ADMIN_BOOT_PASSWORD", "admin"),
        )
    except Exception:
        pass

    # ===== Inyectar roles al contexto de cada request (para menús/permisos) =====
    @app.before_request
    def inject_roles():
        g.user_roles = user_roles(int(current_user.id)) if current_user.is_authenticated else set()

    # ===== Blueprints =====
    # Auth / Registro
    from python.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix="")

    from python.registro import bp as registro_bp
    app.register_blueprint(registro_bp, url_prefix="/registro")

    # Administración (gestión de usuarios/roles)
    from python.admin import bp as admin_bp          # define url_prefix="/admin"
    app.register_blueprint(admin_bp)

    # Flujo de negocio (OT + abonos + facturación)
    from python.orden import bp as orden_bp          # url_prefix en el archivo (/orden)
    from python.facturacion import bp as fact_bp     # url_prefix en el archivo (/facturacion)
    app.register_blueprint(orden_bp)
    app.register_blueprint(fact_bp)

    # Catálogos y otros formularios
    from python.cliente import bp as cliente_bp
    from python.equipo import bp as equipo_bp
    # from python.orden_trabajo import bp as orden_trabajo_bp  # No registrar si ya usas python/orden.py
    from python.detalle_servicio import bp as detalle_servicio_bp
    from python.detalle_repuesto import bp as detalle_repuesto_bp
    from python.abono import bp as abono_bp
    from python.caja import bp as caja_bp
    from python.mov_caja import bp as mov_caja_bp
    from python.comprobante import bp as comprobante_bp
    from python.cat_servicio import bp as cat_servicio_bp
    from python.repuesto import bp as repuesto_bp

    app.register_blueprint(cliente_bp, url_prefix="/cliente")
    app.register_blueprint(equipo_bp, url_prefix="/equipo")
    # app.register_blueprint(orden_trabajo_bp, url_prefix="/orden")  # <- evitar duplicado con python/orden.py
    app.register_blueprint(detalle_servicio_bp, url_prefix="/detalle-servicio")
    app.register_blueprint(detalle_repuesto_bp, url_prefix="/detalle-repuesto")
    app.register_blueprint(abono_bp, url_prefix="/abono")
    app.register_blueprint(caja_bp, url_prefix="/caja")
    app.register_blueprint(mov_caja_bp, url_prefix="/mov-caja")
    app.register_blueprint(comprobante_bp, url_prefix="/comprobante")
    app.register_blueprint(cat_servicio_bp, url_prefix="/cat-servicio")
    app.register_blueprint(repuesto_bp, url_prefix="/repuesto")

    # ===== Rutas base =====
    @app.get("/")
    @login_required
    def index():
        return render_template("index.html")

    @app.get("/ping")
    def ping():
        return {"status": "ok"}

    @app.get("/register")
    def register_redirect():
        return redirect(url_for("registro.form"))

    # Diagnóstico DB
    @app.get("/dbcheck")
    def dbcheck():
        try:
            cn = get_conn(); cur = cn.cursor()
            cur.execute("SELECT 1"); cur.fetchone()
            cur.close(); cn.close()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500

    # (Opcional) Página amigable para 403
    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("403.html"), 403

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=bool(int(os.getenv("FLASK_DEBUG", "1"))))
