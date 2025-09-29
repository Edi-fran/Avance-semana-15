# python/orden_trabajo.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Any, Tuple
import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from python.conexion import query, query_one, execute

bp = Blueprint("orden_trabajo", __name__, template_folder="../templates")

TABLE = "orden_trabajo"
AUDIT_COLS = {"creado_en", "creado_por", "actualizado_en", "actualizado_por"}

# =========================
#  Infra POO
# =========================
def _db() -> str:
    r = query_one("SELECT DATABASE() AS db")
    return r["db"]

@dataclass
class Column:
    name: str
    data_type: str
    column_type: str
    is_nullable: bool
    extra: str
    char_len: int | None

    @property
    def is_ai(self) -> bool:
        return "auto_increment" in (self.extra or "").lower()

@dataclass
class Spec:
    kind: str           # input | textarea | select | checkbox
    type: str | None    # text | number | date | datetime-local ...
    step: str | None
    options: List[str] | None
    is_ai: bool
    nullable: bool

class TableMeta:
    def __init__(self, table: str):
        self.table = table
        self.schema = _db()
        self.columns: List[Column] = self._load_columns()
        self.pk: str | None = self._load_pk()

    def _load_columns(self) -> List[Column]:
        rows = query(
            """
            SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE, EXTRA, CHARACTER_MAXIMUM_LENGTH
            FROM information_schema.columns
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
            ORDER BY ORDINAL_POSITION
            """,
            (self.schema, self.table),
        )
        out: List[Column] = []
        for r in rows:
            out.append(
                Column(
                    name=r["COLUMN_NAME"],
                    data_type=(r["DATA_TYPE"] or "").lower(),
                    column_type=(r["COLUMN_TYPE"] or "").lower(),
                    is_nullable=(r["IS_NULLABLE"] == "YES"),
                    extra=(r["EXTRA"] or ""),
                    char_len=r["CHARACTER_MAXIMUM_LENGTH"],
                )
            )
        return out

    def _load_pk(self) -> str | None:
        r = query_one(
            """
            SELECT k.COLUMN_NAME AS pk
            FROM information_schema.table_constraints t
            JOIN information_schema.key_column_usage k
              ON t.CONSTRAINT_NAME=k.CONSTRAINT_NAME
             AND t.TABLE_SCHEMA=k.TABLE_SCHEMA
             AND t.TABLE_NAME=k.TABLE_NAME
            WHERE t.TABLE_SCHEMA=%s AND t.TABLE_NAME=%s
              AND t.CONSTRAINT_TYPE='PRIMARY KEY'
            LIMIT 1
            """,
            (self.schema, self.table),
        )
        return r["pk"] if r else None

    @staticmethod
    def _parse_enum(column_type: str) -> List[str]:
        m = re.match(r"enum\((.+)\)", (column_type or "").lower())
        if not m:
            return []
        return [s.strip().strip("'") for s in m.group(1).split(",")]

    def spec_for(self, col: Column) -> Spec:
        dt, ctype, extra = col.data_type, col.column_type, (col.extra or "").lower()
        spec = Spec(kind="input", type="text", step=None, options=None, is_ai="auto_increment" in extra, nullable=col.is_nullable)

        if dt in ("tinyint", "smallint", "mediumint", "int", "bigint"):
            if dt == "tinyint" and re.match(r"tinyint\(1\)", ctype):
                spec.kind, spec.type = "checkbox", None
            else:
                spec.type = "number"
        elif dt in ("decimal", "float", "double"):
            spec.type, spec.step = "number", "any"
        elif dt == "date":
            spec.type = "date"
        elif dt in ("datetime", "timestamp"):
            spec.type = "datetime-local"
        elif dt.endswith("text"):
            spec.kind, spec.type = "textarea", None
        elif ctype.startswith("enum("):
            spec.kind, spec.type, spec.options = "select", None, self._parse_enum(ctype)
        return spec

class FKHelper:
    def __init__(self, table: str):
        self.table = table
        self.schema = _db()
        self.map = self._load_fks()  # {col: {'ref_table':..., 'ref_col':...}}

    def _load_fks(self) -> Dict[str, Dict[str, str]]:
        rows = query(
            """
            SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
            FROM information_schema.key_column_usage
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL
            """,
            (self.schema, self.table),
        )
        m: Dict[str, Dict[str, str]] = {}
        for r in rows:
            m[r["COLUMN_NAME"]] = {
                "ref_table": r["REFERENCED_TABLE_NAME"],
                "ref_col": r["REFERENCED_COLUMN_NAME"],
            }
        return m

    def _label_col(self, ref_table: str) -> str:
        cols = query(
            """
            SELECT COLUMN_NAME, DATA_TYPE
            FROM information_schema.columns
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
            ORDER BY ORDINAL_POSITION
            """,
            (self.schema, ref_table),
        )
        prefs = {"nombre_completo", "nombre", "razon_social", "descripcion", "detalle", "modelo", "marca", "usuario_login", "email"}
        for c in cols:
            if c["DATA_TYPE"] in ("varchar", "text", "char") and c["COLUMN_NAME"] in prefs:
                return c["COLUMN_NAME"]
        for c in cols:
            if c["DATA_TYPE"] in ("varchar", "text", "char"):
                return c["COLUMN_NAME"]
        return cols[0]["COLUMN_NAME"] if cols else "id"

    def options(self, ref_table: str, ref_col: str) -> List[Dict[str, Any]]:
        has_activo = query_one(
            """
            SELECT 1 AS ok FROM information_schema.columns
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME='activo' LIMIT 1
            """,
            (self.schema, ref_table),
        )
        label_col = self._label_col(ref_table)
        where = "WHERE `activo`=1" if has_activo else ""
        return query(f"SELECT `{ref_col}` AS id, `{label_col}` AS label FROM `{ref_table}` {where} ORDER BY label")

class FormCodec:
    @staticmethod
    def to_form(col: Column, val: Any) -> Any:
        if val is None:
            return ""
        if col.data_type in ("datetime", "timestamp"):
            s = str(val).replace(" ", "T")
            return s[:16]
        if col.data_type == "tinyint" and re.match(r"tinyint\(1\)", col.column_type or ""):
            return bool(val)
        return val

    @staticmethod
    def to_sql(col: Column, form: Dict[str, Any]) -> Any:
        name = col.name
        raw = form.get(name)

        # checkbox
        if col.data_type == "tinyint" and re.match(r"tinyint\(1\)", col.column_type or ""):
            return 1 if form.get(name) in ("on", "1", "true", "True") else 0

        # vacíos
        if raw in (None, ""):
            return None if col.is_nullable else ""

        if col.data_type in ("tinyint", "smallint", "mediumint", "int", "bigint"):
            try:
                return int(raw)
            except:
                return 0
        if col.data_type in ("decimal", "float", "double"):
            try:
                return float(raw)
            except:
                return 0.0
        if col.data_type in ("date", "datetime", "timestamp"):
            if "T" in raw:
                raw = raw.replace("T", " ") + ":00"
            return raw
        return raw

# =========================
#  Vistas
# =========================
@bp.get("/")
@login_required
def form():
    meta = TableMeta(TABLE)
    fkh = FKHelper(TABLE)

    record_id = request.args.get(meta.pk) if meta.pk else None
    values: Dict[str, Any] = {c.name: "" for c in meta.columns}

    if record_id and meta.pk:
        row = query_one(f"SELECT * FROM `{TABLE}` WHERE `{meta.pk}`=%s", (record_id,))
        if not row:
            abort(404, "Registro no encontrado.")
        for c in meta.columns:
            values[c.name] = FormCodec.to_form(c, row[c.name])

    specs: Dict[str, Spec] = {c.name: meta.spec_for(c) for c in meta.columns}

    # Para columnas FK, las mostramos como select con opciones (id,label)
    fk_options: Dict[str, List[Dict[str, Any]]] = {}
    for col_name, info in fkh.map.items():
        specs[col_name].kind = "select"
        specs[col_name].options = None
        fk_options[col_name] = fkh.options(info["ref_table"], info["ref_col"])

    mode = "edit" if record_id else "create"
    return render_template(
        "form_orden_trabajo.html",
        tabla=TABLE,
        cols=meta.columns,
        pk=meta.pk,
        values=values,
        specs=specs,
        mode=mode,
        audit_cols=AUDIT_COLS,
        fks=fkh.map,
        fk_options=fk_options,
    )

@bp.post("/guardar")
@login_required
def guardar():
    meta = TableMeta(TABLE)
    record_id = request.form.get(meta.pk) if meta.pk else None

    # Columnas editables (sin AI y sin auditoría)
    edit_cols: List[Column] = [c for c in meta.columns if (not c.is_ai) and (c.name not in AUDIT_COLS)]
    names = [c.name for c in edit_cols]
    vals  = [FormCodec.to_sql(c, request.form) for c in edit_cols]

    try:
        if record_id and meta.pk:
            sets = ", ".join(f"`{n}`=%s" for n in names)
            # actualizado_por si existe
            if any(c.name == "actualizado_por" for c in meta.columns):
                sets += ", `actualizado_por`=%s"
                vals.append(int(current_user.id))
            execute(
                f"UPDATE `{TABLE}` SET {sets} WHERE `{meta.pk}`=%s",
                tuple(vals + [record_id]),
            )
            flash("Orden de trabajo actualizada correctamente.", "success")
        else:
            insert_names = list(names)
            insert_vals  = list(vals)
            # creado_por si existe
            if any(c.name == "creado_por" for c in meta.columns):
                insert_names.append("creado_por")
                insert_vals.append(int(current_user.id))

            cols_sql = ", ".join(f"`{n}`" for n in insert_names)
            ph = ", ".join(["%s"] * len(insert_vals))
            _, new_id = execute(
                f"INSERT INTO `{TABLE}` ({cols_sql}) VALUES ({ph})",
                tuple(insert_vals)
            )
            flash(f"Orden de trabajo creada correctamente (ID {new_id}).", "success")

    except Exception as e:
        flash(f"No se pudo guardar: {e}", "danger")

    return redirect(url_for("index"))
