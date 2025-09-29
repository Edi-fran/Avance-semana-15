# python/orden.py
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from mysql.connector import Error
from decimal import Decimal
from python.conexion import get_conn
from python.authz import roles_required

bp = Blueprint("orden", __name__, url_prefix="/orden")

# -------- helpers ----------
def _cols(cur, table: str) -> set[str]:
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name=%s
    """, (table,))
    return {r[0] for r in cur.fetchall()}

def _col_type(cur, table: str, column: str) -> str | None:
    cur.execute("""
        SELECT DATA_TYPE
        FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name=%s AND column_name=%s
    """, (table, column))
    r = cur.fetchone()
    return r[0] if r else None

def _equipo_cols(cur):
    cols = _cols(cur, "equipo")
    imei   = next((c for c in ("imei","imei1","imei_equipo","n_imei") if c in cols), None)
    serie  = next((c for c in ("serie","serial","nro_serie","num_serie","numero_serie") if c in cols), None)
    modelo = "modelo" if "modelo" in cols else next((c for c in ("modelo_equipo","modelo_device") if c in cols), None)
    return imei, serie, modelo

def _orden_cols(cur):
    cols = _cols(cur, "orden_trabajo")
    desc_col = next((c for c in (
        "descripcion","detalle","detalles","observacion","observaciones",
        "comentario","comentarios","diagnostico","problema","descripcion_ot",
        "descripcion_falla","nota","notas"
    ) if c in cols), None)
    return {
        "cols_set": cols,
        "desc_col": desc_col,
        "fecha_recepcion": next((c for c in ("fecha_recepcion","f_recepcion","fecha_ingreso") if c in cols), None),
        "tecnico_col":     next((c for c in ("id_tecnico","tecnico_id","asignado_a") if c in cols), None),
        "prestado_id":     next((c for c in ("id_equipo_prestado","equipo_prestado_id") if c in cols), None),
        "estado":          "estado" if "estado" in cols else None,
        "creado_por":      "creado_por" if "creado_por" in cols else None,
        "creado_en":       next((c for c in ("creado_en","created_at","fecha_creacion") if c in cols), None),
    }

def _abono_cols(cur):
    cols = _cols(cur, "abono")
    return {
        "fecha":      next((c for c in ("creado_en","fecha","fecha_abono","created_at") if c in cols), None),
        "id_usuario": "id_usuario" if "id_usuario" in cols else None,
    }

def _usuarios_tecnicos(cur):
    cur.execute("SELECT COUNT(*) FROM rol WHERE nombre='tecnico' AND activo=1")
    solo_tecnicos = cur.fetchone()[0] > 0
    if solo_tecnicos:
        cur.execute("""
            SELECT u.id_usuario, u.usuario_login, u.nombre_completo
            FROM usuario u
            JOIN usuario_rol ur ON ur.id_usuario=u.id_usuario
            JOIN rol r ON r.id_rol=ur.id_rol
            WHERE r.nombre='tecnico' AND r.activo=1 AND u.activo=1
            ORDER BY COALESCE(u.nombre_completo, u.usuario_login)
        """)
    else:
        cur.execute("""
            SELECT u.id_usuario, u.usuario_login, u.nombre_completo
            FROM usuario u
            WHERE u.activo=1
            ORDER BY COALESCE(u.nombre_completo, u.usuario_login)
        """)
    return cur.fetchall()

# ----------------- vistas -----------------
@bp.get("/nueva")
@login_required
@roles_required("administrador", "facturador")
def nueva():
    cn = get_conn()
    cur = cn.cursor(dictionary=True)

    # clientes
    cur.execute("""
        SELECT id_cliente,
               CONCAT(nombres,' ',COALESCE(apellidos,'')) AS nombre,
               identificacion
        FROM cliente
        ORDER BY nombre
    """)
    clientes = cur.fetchall()

    # equipos
    cur2 = cn.cursor()
    i, s, m = _equipo_cols(cur2)
    cur2.close()

    campos = ["e.id_equipo","e.id_cliente","CONCAT(c.nombres,' ',COALESCE(c.apellidos,'')) AS cliente"]
    if m: campos.append(f"e.{m} AS modelo")
    if i: campos.append(f"e.{i} AS imei")
    if s: campos.append(f"e.{s} AS serie")
    cur.execute(f"""
        SELECT {', '.join(campos)}
        FROM equipo e
        LEFT JOIN cliente c ON c.id_cliente=e.id_cliente
        ORDER BY cliente, {m or 'e.id_equipo'}
    """)
    equipos = cur.fetchall()

    cur3 = cn.cursor()
    tecnicos = _usuarios_tecnicos(cur3)
    ordc = _orden_cols(cur3)
    cur3.close()

    cur.close(); cn.close()
    return render_template("orden_nueva.html",
                           clientes=clientes, equipos=equipos,
                           tecnicos=tecnicos, ordc=ordc)

@bp.post("/crear")
@login_required
@roles_required("administrador", "facturador")
def crear():
    id_cliente  = request.form.get("id_cliente", type=int)
    id_equipo   = request.form.get("id_equipo", type=int)  # opcional
    descripcion = (request.form.get("descripcion") or "").strip()
    abono_monto = request.form.get("abono_monto", type=float) or 0.0
    id_tecnico  = request.form.get("id_tecnico", type=int)

    prestar             = request.form.get("prestar_equipo") == "1"
    equipo_prestado_id  = request.form.get("equipo_prestado_id", type=int)
    prest_modelo        = (request.form.get("prest_modelo") or "").strip()
    prest_imei          = (request.form.get("prest_imei") or "").strip()
    prest_serie         = (request.form.get("prest_serie") or "").strip()

    if not id_cliente or not descripcion:
        flash("Selecciona un cliente e ingresa la descripción.", "warning")
        return redirect(url_for("orden.nueva"))

    cn = get_conn(); cur = cn.cursor()
    try:
        ordc   = _orden_cols(cur)
        abcols = _abono_cols(cur)
        i, s, m  = _equipo_cols(cur)

        # Crear “equipo prestado rápido” si aplica
        if prestar and not equipo_prestado_id and (prest_modelo or prest_imei or prest_serie):
            cols_e, vals_e = ["id_cliente"], [id_cliente]
            if m and prest_modelo: cols_e.append(m); vals_e.append(prest_modelo)
            if i and prest_imei:   cols_e.append(i); vals_e.append(prest_imei)
            if s and prest_serie:  cols_e.append(s); vals_e.append(prest_serie)
            cur.execute(f"INSERT INTO equipo ({', '.join(cols_e)}) VALUES ({', '.join(['%s']*len(vals_e))})", vals_e)
            equipo_prestado_id = cur.lastrowid

        # INSERT OT (con creado_por y fecha_recepcion autom.)
        cols, ph, vals = ["id_cliente"], ["%s"], [id_cliente]
        if "id_equipo" in ordc["cols_set"]:
            cols += ["id_equipo"];  ph += ["%s"]; vals += [id_equipo]
        if ordc["desc_col"]:
            cols += [ordc["desc_col"]]; ph += ["%s"]; vals += [descripcion]
        if ordc["estado"]:
            cols += ["estado"]; ph += ["%s"]; vals += ["ABIERTA"]
        if ordc["creado_por"]:
            cols += ["creado_por"]; ph += ["%s"]; vals += [int(current_user.id)]
        if ordc["tecnico_col"] and id_tecnico:
            cols += [ordc["tecnico_col"]]; ph += ["%s"]; vals += [id_tecnico]
        if ordc["prestado_id"] and equipo_prestado_id:
            cols += [ordc["prestado_id"]]; ph += ["%s"]; vals += [equipo_prestado_id]
        if ordc["fecha_recepcion"]:
            tipo = _col_type(cur, "orden_trabajo", ordc["fecha_recepcion"]) or ""
            fn = "NOW()" if tipo in ("timestamp","datetime") else "CURDATE()"
            cols += [ordc["fecha_recepcion"]]
            ph   += [fn]  # función SQL directa, sin placeholder

        cur.execute(
            f"INSERT INTO orden_trabajo ({', '.join(cols)}) VALUES ({', '.join(ph)})",
            vals
        )
        id_orden = cur.lastrowid

        # Abono opcional
        if abono_monto and abono_monto > 0:
            ab_cols, ab_ph, ab_vals = ["id_orden","monto"], ["%s","%s"], [id_orden, str(abono_monto)]
            if abcols["id_usuario"]:
                ab_cols += ["id_usuario"]; ab_ph += ["%s"]; ab_vals += [int(current_user.id)]
            if abcols["fecha"]:
                t = _col_type(cur, "abono", abcols["fecha"]) or ""
                fn = "NOW()" if t in ("timestamp","datetime") else "CURDATE()"
                ab_cols += [abcols["fecha"]]; ab_ph += [fn]
            cur.execute(
                f"INSERT INTO abono ({', '.join(ab_cols)}) VALUES ({', '.join(ab_ph)})",
                ab_vals
            )

        cn.commit()
        flash(f"Orden creada (# {id_orden}).", "success")
        return redirect(url_for("orden.imprimir", id_orden=id_orden))

    except Error as e:
        cn.rollback()
        flash(f"No se pudo crear la orden: {e}", "danger")
        return redirect(url_for("orden.nueva"))
    finally:
        try:
            cur.close(); cn.close()
        except Exception:
            pass

# -------------------------------------------------
# Ruta puente: /orden/imprimir?id=123 -> /orden/imprimir/123
# -------------------------------------------------
@bp.get("/imprimir")
@login_required
@roles_required("administrador", "facturador")
def imprimir_qs():
    id_orden = request.args.get("id", type=int)
    if not id_orden:
        flash("Ingresa el N° de OT para imprimir.", "warning")
        return redirect(url_for("index"))
    autoprint = request.args.get("autoprint", default=None)
    return redirect(url_for("orden.imprimir", id_orden=id_orden, autoprint=autoprint))

# =======================
#  IMPRIMIR (CORREGIDO)
# =======================
@bp.get("/imprimir/<int:id_orden>")
@login_required
@roles_required("administrador", "facturador")
def imprimir(id_orden: int):
    cn = get_conn(); cur = cn.cursor(dictionary=True)

    # Descubrir columnas reales
    cur2 = cn.cursor()
    ordc = _orden_cols(cur2)       # columnas reales de orden_trabajo
    i, s, m = _equipo_cols(cur2)   # imei/serie/modelo reales en equipo
    abcols = _abono_cols(cur2)     # fecha real en abono
    cur2.close()

    # Equipo principal a mostrar
    eq_sel = []
    if m: eq_sel.append(f"e.{m} AS eq_modelo")
    if i: eq_sel.append(f"e.{i} AS eq_imei")
    if s: eq_sel.append(f"e.{s} AS eq_serie")
    eq_sel = (", " + ", ".join(eq_sel)) if eq_sel else ""

    # Equipo prestado (si existe en tu esquema)
    prest_join, prest_sel = "", ""
    if ordc.get("prestado_id"):
        prest_join = f"LEFT JOIN equipo ep ON ep.id_equipo = o.{ordc['prestado_id']}"
        p = []
        if m: p.append(f"ep.{m} AS prest_modelo")
        if i: p.append(f"ep.{i} AS prest_imei")
        if s: p.append(f"ep.{s} AS prest_serie")
        prest_sel = (", " + ", ".join(p)) if p else ""

    # Técnico (si hay columna)
    tec_sel = ""
    if ordc.get("tecnico_col"):
        tec_sel = f", (SELECT u.usuario_login FROM usuario u WHERE u.id_usuario=o.{ordc['tecnico_col']}) AS tecnico_login"

    # Fecha de recepción (o creado_en como fallback)
    rec_sel = ""
    if ordc.get("fecha_recepcion"):
        rec_sel = f", o.{ordc['fecha_recepcion']} AS fecha_recepcion"
    elif ordc.get("creado_en"):
        rec_sel = f", o.{ordc['creado_en']} AS fecha_recepcion"

    # Descripción y creado_en protegidos con alias fijos
    desc_expr   = f"o.{ordc['desc_col']} AS descripcion" if ordc.get('desc_col') else "NULL AS descripcion"
    creado_expr = f"o.{ordc['creado_en']} AS creado_en" if ordc.get('creado_en') else "NULL AS creado_en"

    # ---- Cabecera de la orden ----
    cur.execute(f"""
        SELECT o.id_orden,
               {desc_expr},
               o.estado,
               {creado_expr},
               CONCAT(c.nombres,' ',COALESCE(c.apellidos,'')) AS cli_nombre,
               c.identificacion AS cli_cedula,
               c.telefono AS cli_tel,
               c.email AS cli_email
               {rec_sel}{tec_sel}{eq_sel}{prest_sel}
        FROM orden_trabajo o
        JOIN cliente c ON c.id_cliente=o.id_cliente
        LEFT JOIN equipo e ON e.id_equipo=o.id_equipo
        {prest_join}
        WHERE o.id_orden=%s
    """, (id_orden,))
    ot = cur.fetchone()
    if not ot:
        cur.close(); cn.close()
        flash("Orden no encontrada.", "warning")
        return redirect(url_for("index"))

    # ---- Ítems (SERVICIOS + REPUESTOS) ----
    cur.execute("""
        SELECT
          'SERVICIO' AS tipo,
          cs.nombre       AS item,
          cs.descripcion  AS descripcion_item,
          ds.cantidad,
          ds.precio_unit  AS precio_unit,
          ds.subtotal
        FROM detalle_servicio ds
        JOIN cat_servicio cs ON cs.id_servicio = ds.id_servicio
        WHERE ds.id_orden=%s

        UNION ALL

        SELECT
          'REPUESTO' AS tipo,
          r.nombre       AS item,
          NULL           AS descripcion_item,
          dr.cantidad,
          dr.precio_unit AS precio_unit,
          dr.subtotal
        FROM detalle_repuesto dr
        JOIN repuesto r ON r.id_repuesto = dr.id_repuesto
        WHERE dr.id_orden=%s

        ORDER BY tipo, item
    """, (id_orden, id_orden))
    items = cur.fetchall()

    # ---- Abonos ----
    ab_fecha = abcols["fecha"] or "fecha"
    cur.execute(
        f"""
        SELECT {ab_fecha} AS creado_en,
               monto,
               metodo,
               referencia,
               estado,
               observacion
        FROM abono
        WHERE id_orden=%s
        ORDER BY {ab_fecha} ASC
        """,
        (id_orden,)
    )
    abonos = cur.fetchall()

    cur.close(); cn.close()

    # ---- Totales ----
    subtotal = sum(Decimal(str(i["subtotal"])) for i in items) if items else Decimal("0")
    pagado   = sum(Decimal(str(a["monto"])) for a in abonos) if abonos else Decimal("0")
    saldo    = subtotal - pagado

    return render_template(
        "orden_imprimir.html",
        ot=ot, items=items, abonos=abonos,
        subtotal=subtotal, pagado=pagado, saldo=saldo
    )
