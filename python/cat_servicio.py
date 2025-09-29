# python/cat_servicio.py
from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Any, Dict, List

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from python.conexion import query, query_one, execute

bp = Blueprint("cat_servicio", __name__, template_folder="../templates")

TABLE = "cat_servicio"
AUDIT_COLS = {"creado_en", "creado_por", "actualizado_en", "actualizado_por"}

# ---------- infra mínima ----------
def _db() -> str:
    r = query_one("SELECT DATABASE() AS db")
    return r["db"]

@dataclass
class Col:
    name: str
    data_type: str
    column_type: str
    is_nullable: bool
    extra: str

    @property
    def is_ai(self) -> bool:
        return "auto_increment" in (self.extra or "").lower()

def _columns() -> List[Col]:
    rows = query(
        """
        SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE, EXTRA
        FROM information_schema.columns
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
        ORDER BY ORDINAL_POSITION
        """,
        (_db(), TABLE),
    )
    return [
        Col(
            name=r["COLUMN_NAME"],
            data_type=(r["DATA_TYPE"] or "").lower(),
            column_type=(r["COLUMN_TYPE"] or "").lower(),
            is_nullable=(r["IS_NULLABLE"] == "YES"),
            extra=(r["EXTRA"] or ""),
        )
        for r in rows
    ]

def _pk() -> str | None:
    r = query_one(
        """
        SELECT k.COLUMN_NAME AS pk
        FROM information_schema.table_constraints t
        JOIN information_schema.key_column_usage k
          ON t.CONSTRAINT_NAME=k.CONSTRAINT_NAME
         AND t.TABLE_SCHEMA=k.TABLE_SCHEMA
         AND t.TABLE_NAME=k.TABLE_NAME
        WHERE t.TABLE_SCHEMA=%s AND t.TABLE_NAME=%s AND t.CONSTRAINT_TYPE='PRIMARY KEY'
        LIMIT 1
        """,
        (_db(), TABLE),
    )
    return r["pk"] if r else None

def _spec(col: Col) -> Dict[str, Any]:
    """Input spec para el form."""
    dt, ctype = col.data_type, col.column_type
    spec = {"kind": "input", "type": "text", "step": None, "is_ai": col.is_ai}
    if dt in ("tinyint","smallint","mediumint","int","bigint"):
        if dt == "tinyint" and re.match(r"tinyint\(1\)", ctype or ""):
            spec["kind"] = "checkbox"; spec["type"] = None
        else:
            spec["type"] = "number"
    elif dt in ("decimal","float","double"):
        spec["type"] = "number"; spec["step"] = "any"
    elif dt == "date":
        spec["type"] = "date"
    elif dt in ("datetime","timestamp"):
        spec["type"] = "datetime-local"
    elif dt.endswith("text"):
        spec["kind"] = "textarea"; spec["type"] = None
    elif (ctype or "").startswith("enum("):
        spec["kind"] = "select"
        spec["options"] = [s.strip().strip("'") for s in ctype[5:-1].split(",")]
    return spec

def _to_form(col: Col, val: Any) -> Any:
    if val is None: return ""
    if col.data_type in ("datetime","timestamp"):
        return str(val).replace(" ", "T")[:16]
    if col.data_type == "tinyint" and re.match(r"tinyint\(1\)", col.column_type or ""):
        return bool(val)
    return val

def _from_form(col: Col, form) -> Any:
    raw = form.get(col.name)

    # checkbox
    if col.data_type == "tinyint" and re.match(r"tinyint\(1\)", col.column_type or ""):
        return 1 if form.get(col.name) in ("on","1","true","True") else 0

    if raw in (None, ""):
        return None if col.is_nullable else ""

    if col.data_type in ("tinyint","smallint","mediumint","int","bigint"):
        try: return int(raw)
        except: return 0
    if col.data_type in ("decimal","float","double"):
        try: return float(raw)
        except: return 0.0
    if col.data_type in ("date","datetime","timestamp"):
        if "T" in raw: raw = raw.replace("T", " ") + ":00"
        return raw
    return raw

# ---------- vistas ----------
@bp.get("/")
@login_required
def form():
    cols = _columns()
    pk = _pk()
    record_id = request.args.get(pk) if pk else None

    values = {c.name: "" for c in cols}
    if record_id and pk:
        row = query_one(f"SELECT * FROM `{TABLE}` WHERE `{pk}`=%s", (record_id,))
        if not row: abort(404, "Registro no encontrado.")
        for c in cols:
            values[c.name] = _to_form(c, row[c.name])

    specs = {c.name: _spec(c) for c in cols}
    mode = "edit" if record_id else "create"
    return render_template(
        "form_cat_servicio.html",
        tabla=TABLE, cols=cols, pk=pk, values=values,
        specs=specs, mode=mode, audit_cols=AUDIT_COLS
    )

@bp.post("/guardar")
@login_required
def guardar():
    cols = _columns()
    pk = _pk()
    record_id = request.form.get(pk) if pk else None

    edit_cols = [c for c in cols if (not c.is_ai) and (c.name not in AUDIT_COLS)]
    names = [c.name for c in edit_cols]
    vals  = [_from_form(c, request.form) for c in edit_cols]

    try:
        if record_id and pk:
            sets = ", ".join(f"`{n}`=%s" for n in names)
            if any(c.name == "actualizado_por" for c in cols):
                sets += ", `actualizado_por`=%s"
                vals.append(int(current_user.id))
            execute(f"UPDATE `{TABLE}` SET {sets} WHERE `{pk}`=%s", tuple(vals + [record_id]))
            flash("Catálogo de servicio actualizado.", "success")
        else:
            ins_names = list(names)
            ins_vals  = list(vals)
            if any(c.name == "creado_por" for c in cols):
                ins_names.append("creado_por")
                ins_vals.append(int(current_user.id))
            cols_sql = ", ".join(f"`{n}`" for n in ins_names)
            ph = ", ".join(["%s"] * len(ins_vals))
            _, new_id = execute(f"INSERT INTO `{TABLE}` ({cols_sql}) VALUES ({ph})", tuple(ins_vals))
            flash(f"Catálogo de servicio creado (ID {new_id}).", "success")
    except Exception as e:
        flash(f"No se pudo guardar: {e}", "danger")

    return redirect(url_for("index"))

