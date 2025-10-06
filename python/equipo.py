# python/equipo.py
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from python.conexion import query, query_one, execute
import re

bp = Blueprint("equipo", __name__, template_folder="../templates")

TABLE = "equipo"
AUDIT_COLS = {"creado_en", "creado_por", "actualizado_en", "actualizado_por"}

# ---------- Utilidades de metadatos ----------
def _db_name() -> str:
    r = query_one("SELECT DATABASE() AS db")
    return r["db"]

def _meta():
    """Columnas y PK de la tabla equipo."""
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
    rpk = query_one(
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
    pk = rpk["pk"] if rpk else None
    return cols, pk

def _foreign_keys():
    """FKs de equipo -> {col: {'ref_table':..., 'ref_col':...}}"""
    db = _db_name()
    rows = query(
        """
        SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
        FROM information_schema.key_column_usage
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL
        """,
        (db, TABLE),
    )
    fks = {}
    for r in rows:
        fks[r["COLUMN_NAME"]] = {
            "ref_table": r["REFERENCED_TABLE_NAME"],
            "ref_col": r["REFERENCED_COLUMN_NAME"],
        }
    return fks

def _has_column(ref_table: str, col: str) -> bool:
    """¿La tabla tiene esta columna?"""
    return bool(query_one(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        LIMIT 1
        """,
        (ref_table, col),
    ))

# ---------- Etiquetas para selects ----------
def _pick_label_column(ref_table: str) -> str:
    """
    Heurística genérica (fallback) para elegir una columna "bonita" de texto.
    Ampliada para dar prioridad a identificadores personales si existen.
    """
    db = _db_name()
    cols = query(
        """
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.columns
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
        ORDER BY ORDINAL_POSITION
        """,
        (db, ref_table),
    )
    prefs = {
        "identificacion", "cedula", "dni", "documento",
        "nombre_completo", "nombre", "razon_social",
        "descripcion", "detalle", "modelo", "marca",
        "email", "usuario_login"
    }
    # primero, si hay una preferida tipo texto
    for c in cols:
        if c["DATA_TYPE"] in ("varchar", "text", "char") and c["COLUMN_NAME"] in prefs:
            return c["COLUMN_NAME"]
    # luego, cualquier texto
    for c in cols:
        if c["DATA_TYPE"] in ("varchar", "text", "char"):
            return c["COLUMN_NAME"]
    # si nada, la primera
    return cols[0]["COLUMN_NAME"] if cols else "id"

def _fk_options(ref_table: str, ref_col: str):
    """
    Opciones (id, label) para un select de FK.
    Caso especial para tabla 'cliente':
      - Si hay nombres/apellidos y documento (identificacion/cedula/dni/documento):
            'Nombres Apellidos - Documento'
      - Si no hay nombres, muestra solo Documento.
      - Filtro 'activo=1' si existe.
    Resto de tablas usa heurística genérica.
    """
    if ref_table.lower() == "cliente":
        # Detectar columna de documento
        doc_col = next((c for c in ("identificacion", "cedula", "dni", "documento")
                        if _has_column(ref_table, c)), None)
        has_nom = _has_column(ref_table, "nombres")
        has_ape = _has_column(ref_table, "apellidos")

        if doc_col:
            if has_nom or has_ape:
                nombre_expr = "CONCAT(COALESCE(nombres,''),' ',COALESCE(apellidos,''))"
                label_expr = f"CONCAT({nombre_expr}, ' - ', `{doc_col}`)"
            else:
                label_expr = f"`{doc_col}`"
        else:
            label_col = _pick_label_column(ref_table)
            label_expr = f"`{label_col}`"

        where = "WHERE `activo`=1" if _has_column(ref_table, "activo") else ""
        return query(
            f"SELECT `{ref_col}` AS id, {label_expr} AS label "
            f"FROM `{ref_table}` {where} ORDER BY label"
        )

    # Genérico para otras tablas
    label_col = _pick_label_column(ref_table)
    where = "WHERE `activo`=1" if _has_column(ref_table, "activo") else ""
    return query(
        f"SELECT `{ref_col}` AS id, `{label_col}` AS label "
        f"FROM `{ref_table}` {where} ORDER BY label"
    )

# ---------- Mapeo de tipos a widgets ----------
def _parse_enum(column_type: str):
    m = re.match(r"enum\((.+)\)", (column_type or "").lower())
    if not m:
        return []
    return [s.strip().strip("'") for s in m.group(1).split(",")]

def _spec(col):
    dt    = (col["DATA_TYPE"] or "").lower()
    ctype = (col["COLUMN_TYPE"] or "").lower()
    extra = (col["EXTRA"] or "").lower()

    spec = {
        "kind": "input",
        "type": "text",
        "step": None,
        "options": None,
        "is_ai": "auto_increment" in extra,
        "nullable": (col["IS_NULLABLE"] == "YES"),
    }
    if dt in ("tinyint", "smallint", "mediumint", "int", "bigint"):
        if dt == "tinyint" and re.match(r"tinyint\(1\)", ctype):
            spec["kind"] = "checkbox"; spec["type"] = None
        else:
            spec["type"] = "number"
    elif dt in ("decimal", "float", "double"):
        spec["type"] = "number"; spec["step"] = "any"
    elif dt == "date":
        spec["type"] = "date"
    elif dt in ("datetime", "timestamp"):
        spec["type"] = "datetime-local"
    elif dt.endswith("text"):
        spec["kind"] = "textarea"
    elif ctype.startswith("enum("):
        spec["kind"] = "select"; spec["options"] = _parse_enum(ctype)
    return spec

def _row_to_form_value(col, val):
    if val is None:
        return ""
    dt    = (col["DATA_TYPE"] or "").lower()
    ctype = (col["COLUMN_TYPE"] or "").lower()
    if dt in ("datetime", "timestamp"):
        s = str(val).replace(" ", "T")
        return s[:16]  # yyyy-mm-ddThh:mm
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
        return 1 if form.get(name) in ("on", "1", "true", "True") else 0
    if raw in (None, ""):
        return None if col["IS_NULLABLE"] == "YES" else ""

    if dt in ("tinyint", "smallint", "mediumint", "int", "bigint"):
        try:
            return int(raw)
        except:
            return 0
    if dt in ("decimal", "float", "double"):
        try:
            return float(raw)
        except:
            return 0.0
    if dt in ("date", "datetime", "timestamp"):
        if "T" in raw:
            raw = raw.replace("T", " ") + ":00"
        return raw
    return raw

# ---------- Vistas ----------
@bp.get("/")
@login_required
def form():
    cols, pk = _meta()
    fks = _foreign_keys()

    # carga registro para editar si viene ?pk=...
    record_id = request.args.get(pk) if pk else None
    values = {c["COLUMN_NAME"]: "" for c in cols}
    if record_id and pk:
        row = query_one(f"SELECT * FROM `{TABLE}` WHERE `{pk}`=%s", (record_id,))
        if not row:
            abort(404, "Registro no encontrado.")
        values = {
            c["COLUMN_NAME"]: _row_to_form_value(c, row[c["COLUMN_NAME"]])
            for c in cols
        }

    # especificaciones + opciones de FK
    specs = {c["COLUMN_NAME"]: _spec(c) for c in cols}
    fk_options = {}
    for col_name, meta in fks.items():
        specs[col_name]["kind"] = "select"
        specs[col_name]["options"] = None  # lo llena la plantilla con fk_options
        fk_options[col_name] = _fk_options(meta["ref_table"], meta["ref_col"])

    mode = "edit" if record_id else "create"
    return render_template(
        "form_equipo.html",
        tabla=TABLE,
        cols=cols,
        pk=pk,
        values=values,
        specs=specs,
        mode=mode,
        audit_cols=AUDIT_COLS,
        fks=fks,
        fk_options=fk_options,
    )

@bp.post("/guardar")
@login_required
def guardar():
    cols, pk = _meta()
    _ = _foreign_keys()
    record_id = request.form.get(pk) if pk else None

    # columnas editables (sin autoincrement ni audit)
    edit_cols = [
        c for c in cols
        if (not _spec(c)["is_ai"]) and (c["COLUMN_NAME"] not in AUDIT_COLS)
    ]
    names = [c["COLUMN_NAME"] for c in edit_cols]
    vals  = [_form_to_sql_value(c, request.form) for c in edit_cols]

    try:
        if record_id and pk:
            # UPDATE
            sets = ", ".join(f"`{n}`=%s" for n in names)
            if any(c["COLUMN_NAME"] == "actualizado_por" for c in cols):
                sets += ", `actualizado_por`=%s"
                vals.append(int(current_user.id))
            execute(
                f"UPDATE `{TABLE}` SET {sets} WHERE `{pk}`=%s",
                tuple(vals + [record_id]),
            )
            flash("Equipo actualizado correctamente.", "success")
        else:
            # INSERT
            insert_names = list(names)
            insert_vals  = list(vals)
            if any(c["COLUMN_NAME"] == "creado_por" for c in cols):
                insert_names.append("creado_por")
                insert_vals.append(int(current_user.id))

            cols_sql = ", ".join(f"`{n}`" for n in insert_names)
            ph = ", ".join(["%s"] * len(insert_vals))
            _, new_id = execute(
                f"INSERT INTO `{TABLE}` ({cols_sql}) VALUES ({ph})",
                tuple(insert_vals),
            )
            flash(f"Equipo creado correctamente (ID {new_id}).", "success")

    except Exception as e:
        flash(f"No se pudo guardar: {e}", "danger")

    # vuelve al inicio (o cambia a url_for('equipo.form') si prefieres)
    return redirect(url_for("index"))

