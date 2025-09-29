# python/cliente.py
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from python.conexion import query, query_one, execute
import re

bp = Blueprint("cliente", __name__, template_folder="../templates")

TABLE = "cliente"
AUDIT_COLS = {"creado_en", "creado_por", "actualizado_en", "actualizado_por"}  # <- no tocar desde el form

def _db_name() -> str:
    r = query_one("SELECT DATABASE() AS db")
    return r["db"]

def _meta():
    db = _db_name()
    cols = query(
        """
        SELECT c.COLUMN_NAME, c.DATA_TYPE, c.COLUMN_TYPE, c.IS_NULLABLE,
               c.EXTRA, c.CHARACTER_MAXIMUM_LENGTH
        FROM information_schema.columns c
        WHERE c.TABLE_SCHEMA=%s AND c.TABLE_NAME=%s
        ORDER BY c.ORDINAL_POSITION
        """,
        (db, TABLE),
    )
    k = query_one(
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
        (db, TABLE),
    )
    pk = k["pk"] if k else None
    return cols, pk

def _parse_enum(column_type: str):
    m = re.match(r"enum\((.+)\)", (column_type or "").lower())
    if not m: return []
    return [s.strip().strip("'") for s in m.group(1).split(",")]

def _spec(col):
    dt    = (col["DATA_TYPE"] or "").lower()
    ctype = (col["COLUMN_TYPE"] or "").lower()
    extra = (col["EXTRA"] or "").lower()

    spec = {"kind": "input", "type": "text", "step": None,
            "options": None, "is_ai": "auto_increment" in extra,
            "nullable": (col["IS_NULLABLE"] == "YES")}

    if dt in ("tinyint","smallint","mediumint","int","bigint"):
        if dt == "tinyint" and re.match(r"tinyint\(1\)", ctype):
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
        spec["kind"] = "textarea"
    elif ctype.startswith("enum("):
        spec["kind"] = "select"; spec["options"] = _parse_enum(ctype)
    return spec

def _row_to_form_value(col, val):
    if val is None: return ""
    dt    = (col["DATA_TYPE"] or "").lower()
    ctype = (col["COLUMN_TYPE"] or "").lower()
    if dt in ("datetime","timestamp"):
        s = str(val).replace(" ", "T")
        return s[:16]  # YYYY-MM-DDTHH:MM
    if dt == "tinyint" and re.match(r"tinyint\(1\)", ctype):
        return bool(val)
    return val

def _form_to_sql_value(col, form):
    name = col["COLUMN_NAME"]
    spec = _spec(col)
    raw  = form.get(name)
    dt    = (col["DATA_TYPE"] or "").lower()
    ctype = (col["COLUMN_TYPE"] or "").lower()

    if spec["kind"] == "checkbox":
        return 1 if form.get(name) in ("on","1","true","True") else 0

    if raw in (None, ""):
        # Deja NULL si la columna lo permite; así aplican defaults de la BD
        return None if col["IS_NULLABLE"] == "YES" else ""

    if dt in ("tinyint","smallint","mediumint","int","bigint"):
        try: return int(raw)
        except: return 0
    if dt in ("decimal","float","double"):
        try: return float(raw)
        except: return 0.0
    if dt in ("date","datetime","timestamp"):
        if "T" in raw: raw = raw.replace("T", " ") + ":00"
        return raw
    return raw

# ---------- Vistas ----------
@bp.get("/")
@login_required
def form():
    cols, pk = _meta()
    record_id = request.args.get(pk) if pk else None

    values = {c["COLUMN_NAME"]: "" for c in cols}
    if record_id and pk:
        row = query_one(f"SELECT * FROM `{TABLE}` WHERE `{pk}`=%s", (record_id,))
        if not row:
            abort(404, "Registro no encontrado.")
        values = {c["COLUMN_NAME"]: _row_to_form_value(c, row[c["COLUMN_NAME"]]) for c in cols}

    specs = {c["COLUMN_NAME"]: _spec(c) for c in cols}
    mode = "edit" if record_id else "create"
    return render_template(
        "form_cliente.html",
        tabla=TABLE, cols=cols, pk=pk,
        values=values, specs=specs, mode=mode,
        audit_cols=AUDIT_COLS,  # <- para ocultarlas en el template
    )

@bp.post("/guardar")
@login_required
def guardar():
    cols, pk = _meta()
    record_id = request.form.get(pk) if pk else None

    # columnas editables del form (excluye PK auto y columnas de auditoría)
    edit_cols = [
        c for c in cols
        if (not _spec(c)["is_ai"]) and (c["COLUMN_NAME"] not in AUDIT_COLS)
    ]
    field_names = [c["COLUMN_NAME"] for c in edit_cols]
    field_values = [_form_to_sql_value(c, request.form) for c in edit_cols]

    try:
        if record_id and pk:
            # UPDATE: no tocamos creado_*; ON UPDATE ya mantiene actualizado_en
            sets = ", ".join(f"`{name}`=%s" for name in field_names)

            # Si existe actualizado_por, lo seteamos con el usuario actual
            if any(c["COLUMN_NAME"] == "actualizado_por" for c in cols):
                sets += ", `actualizado_por`=%s"
                field_values.append(int(current_user.id))

            execute(
                f"UPDATE `{TABLE}` SET {sets} WHERE `{pk}`=%s",
                tuple(field_values + [record_id])
            )
            flash("Cliente actualizado correctamente.", "success")

        else:
            # INSERT: dejamos que la BD ponga creado_en. Si existe creado_por, lo mandamos.
            insert_names = list(field_names)
            insert_vals  = list(field_values)
            if any(c["COLUMN_NAME"] == "creado_por" for c in cols):
                insert_names.append("creado_por")
                insert_vals.append(int(current_user.id))

            cols_sql = ", ".join(f"`{n}`" for n in insert_names)
            ph = ", ".join(["%s"] * len(insert_vals))
            _, new_id = execute(
                f"INSERT INTO `{TABLE}` ({cols_sql}) VALUES ({ph})",
                tuple(insert_vals)
            )
            flash(f"Cliente creado correctamente (ID {new_id}).", "success")

    except Exception as e:
        flash(f"No se pudo guardar: {e}", "danger")

    return redirect(url_for("index"))
