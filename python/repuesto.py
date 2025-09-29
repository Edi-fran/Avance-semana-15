from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash

bp = Blueprint("repuesto", __name__, template_folder="../templates")

@bp.get("/")
def form():
    return render_template("form_repuesto.html")

@bp.post("/guardar")
def guardar():
    # TODO: insertar/actualizar en BD usando mysql-connector
    # Los campos llegarán en request.form
    flash("Guardado pendiente de implementar", "info")
    return redirect(url_for("index"))
