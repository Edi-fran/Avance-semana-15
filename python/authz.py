# python/authz.py
from __future__ import annotations
from functools import wraps
from typing import Iterable, Set
from flask import flash, redirect, url_for, request, g
from flask_login import current_user
from python.conexion import get_conn

# Mapea sinónimos a un nombre canónico
_ROLE_ALIAS = {
    "administrador": "admin",
}

def _canon(name: str) -> str:
    """Normaliza el nombre de rol (minúsculas + alias)."""
    n = (name or "").strip().lower()
    return _ROLE_ALIAS.get(n, n)

def user_roles(id_usuario: int) -> Set[str]:
    """
    Devuelve el conjunto de nombres de rol ACTIVOS del usuario, en minúsculas.
    Lee de usuario_rol + rol.
    """
    roles: Set[str] = set()
    try:
        cn = get_conn(); cur = cn.cursor()
        cur.execute(
            """
            SELECT r.nombre
            FROM usuario_rol ur
            JOIN rol r ON r.id_rol = ur.id_rol
            WHERE ur.id_usuario=%s AND r.activo=1
            """,
            (id_usuario,),
        )
        for (nombre,) in cur.fetchall():
            roles.add(_canon(nombre))
        cur.close(); cn.close()
    except Exception:
        # Si la DB no está disponible, devolvemos set() sin romper la app
        return set()
    return roles

def _ensure_roles_loaded() -> Set[str]:
    """Usa roles precargados en g si existen (app.before_request), si no los consulta."""
    if not current_user.is_authenticated:
        return set()
    roles = getattr(g, "user_roles", None)
    if roles is None:
        try:
            roles = user_roles(int(current_user.id))
        except Exception:
            roles = set()
    return roles

def role_required(*required: Iterable[str]):
    """
    Decorador: exige AL MENOS uno de los roles indicados.
    Ejemplos:
      @role_required("admin")
      @role_required("admin", "facturador")
    """
    needed = { _canon(r) for r in required if r }

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                # Volver al login preservando la ruta original
                return redirect(url_for("auth.login_form", next=request.path))

            roles = _ensure_roles_loaded()
            # Si el usuario no tiene ninguno de los requeridos, negamos
            if roles.isdisjoint(needed):
                flash("No tienes permisos para acceder a esta sección.", "warning")
                return redirect(url_for("index"))

            return view_func(*args, **kwargs)
        return wrapped
    return decorator

# Alias por compatibilidad con tu código previo
def roles_required(*required: Iterable[str]):
    return role_required(*required)

# Atajos de uso común
admin_required      = role_required("admin")                 # solo admin
facturador_required = role_required("admin", "facturador")   # admin también puede facturar

def has_role(name: str) -> bool:
    """Helper para usar en vistas/plantillas: {% if has_role('admin') %} ... {% endif %}"""
    return _canon(name) in _ensure_roles_loaded()

__all__ = [
    "user_roles",
    "role_required",
    "roles_required",
    "admin_required",
    "facturador_required",
    "has_role",
]

