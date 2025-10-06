# python/facturacion.py
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from mysql.connector import Error
from python.conexion import get_conn
from python.authz import roles_required
import decimal, os

bp = Blueprint("facturacion", __name__, url_prefix="/facturacion")

def _iva_pct() -> decimal.Decimal:
    try:
        return decimal.Decimal(os.getenv("IVA_PORCENTAJE", "15")) / decimal.Decimal(100)
    except Exception:
        return decimal.Decimal("0.15")

# ---------- utilidades ----------
def _row_name(r):
    """leer COLUMN_NAME venga como tupla o dict"""
    if isinstance(r, (tuple, list)): return r[0]
    return r.get("COLUMN_NAME")

def _cols(cur, table: str) -> set[str]:
    cur.execute("""
        SELECT COLUMN_NAME FROM information_schema.columns
        WHERE table_schema=DATABASE() AND table_name=%s
    """, (table,))
    return { _row_name(r) for r in cur.fetchall() }

def _first(cols: set[str], candidates: list[str], default: str|None=None):
    for c in candidates:
        if c in cols: return c
    return default

# --- introspección ligera ---
def _orden_cols(cur):
    cols = _cols(cur, "orden_trabajo")
    return {
        "desc":            _first(cols, ["descripcion","detalle","observaciones","diagnostico"]),
        "estado":          _first(cols, ["estado"]),
        "creado_en":       _first(cols, ["creado_en","created_at","fecha_creacion"]),
        "fecha_recepcion": _first(cols, ["fecha_recepcion","fecha_ingreso"])
    }

def _cliente_cols(cur):
    cols = _cols(cur, "cliente")
    return {
        "identificacion":  _first(cols, ["identificacion","cedula"]),
        "tel":             _first(cols, ["telefono","celular"]),
    }

def _cat_servicio(cur):
    """
    Devuelve (id_col, label_col, price_col, rows) de cat_servicio.
    Soporta: id_servicio | nombre/descripcion | precio_base/precio_ref/precio_unitario/precio
    """
    cols = _cols(cur, "cat_servicio")
    if not cols: return (None, None, None, [])

    idc   = _first(cols, [c for c in cols if c.startswith("id_")], None) or "id_servicio"
    label = _first(cols, ["nombre","descripcion"]) or next(iter(cols))
    price = _first(cols, ["precio_base","precio_ref","precio_unitario","precio","valor","monto"])

    # construir SELECT seguro
    sel = [f"{idc} AS id", f"{label} AS d"]
    if price:
        sel.append(f"{price} AS p")
    else:
        sel.append("0 AS p")

    # filtrar por activo si existe
    where = "WHERE activo=1" if "activo" in cols else ""
    cur.execute(f"SELECT {', '.join(sel)} FROM cat_servicio {where} ORDER BY d")
    rows = cur.fetchall()
    return (idc, label, price or "p", rows)

def _repuesto_cat(cur):
    """
    Devuelve (id_col, label_col, price_col, rows) de repuesto.
    Soporta: id_repuesto | nombre/descripcion | precio_unitario/precio_base/precio
    """
    cols = _cols(cur, "repuesto")
    if not cols: return (None, None, None, [])

    idc   = _first(cols, [c for c in cols if c.startswith("id_")], None) or "id_repuesto"
    label = _first(cols, ["nombre","descripcion"]) or next(iter(cols))
    price = _first(cols, ["precio_unitario","precio_base","precio","valor","monto"])

    sel = [f"{idc} AS id", f"{label} AS d"]
    if price:
        sel.append(f"{price} AS p")
    else:
        sel.append("0 AS p")

    where = "WHERE activo=1" if "activo" in cols else ""
    cur.execute(f"SELECT {', '.join(sel)} FROM repuesto {where} ORDER BY d")
    rows = cur.fetchall()
    return (idc, label, price or "p", rows)

# ---------- vistas ----------
@bp.get("/emitir/<int:id_orden>")
@login_required
@roles_required("administrador", "facturador")
def emitir_form(id_orden: int):
    cn = get_conn()
    cur_i = cn.cursor()               # introspección (tuplas)
    cur   = cn.cursor(dictionary=True)

    oc = _orden_cols(cur_i); cc = _cliente_cols(cur_i)

    cur.execute(f"""
        SELECT o.id_orden,
               {('o.'+oc['desc']) if oc['desc'] else 'NULL'} AS descripcion,
               {('o.'+oc['estado']) if oc['estado'] else 'NULL'} AS estado,
               {('o.'+oc['creado_en']) if oc['creado_en'] else 'NULL'} AS creado_en,
               {('o.'+oc['fecha_recepcion']) if oc['fecha_recepcion'] else 'NULL'} AS fecha_recepcion,
               o.id_cliente,
               CONCAT(c.nombres,' ',COALESCE(c.apellidos,'')) AS cli_nombre,
               {('c.'+cc['identificacion']) if cc['identificacion'] else 'NULL'} AS cli_identificacion,
               {('c.'+cc['tel']) if cc['tel'] else 'NULL'} AS cli_tel,
               c.email AS cli_email
        FROM orden_trabajo o
        JOIN cliente c ON c.id_cliente=o.id_cliente
        WHERE o.id_orden=%s
    """, (id_orden,))
    ot = cur.fetchone()
    if not ot:
        cur.close(); cur_i.close(); cn.close()
        flash("No existe la Orden indicada.", "warning")
        return redirect(url_for("index"))

    # detalle servicios
    cur.execute("""
        SELECT id_detalle_servicio AS id, descripcion, cantidad, precio_unitario
        FROM detalle_servicio WHERE id_orden=%s
    """, (id_orden,))
    servicios = cur.fetchall()

    # detalle repuestos
    cur.execute("""
        SELECT id_detalle_repuesto AS id, descripcion, cantidad, precio_unitario
        FROM detalle_repuesto WHERE id_orden=%s
    """, (id_orden,))
    repuestos = cur.fetchall()

    # abonos
    cur.execute("""
        SELECT id_abono, monto, creado_en AS fecha, metodo
        FROM abono WHERE id_orden=%s ORDER BY creado_en
    """, (id_orden,))
    abonos = cur.fetchall()

    # catálogos (usar cursor de tuplas para introspección fiable)
    sid, sdesc, sprice, cat_serv = _cat_servicio(cn.cursor())
    rid, rdesc, rprice, cat_rep  = _repuesto_cat(cn.cursor())

    cur.close(); cur_i.close(); cn.close()

    dinero = lambda v: decimal.Decimal(str(v or 0))
    subtotal_servicios = sum(dinero(s["cantidad"]) * dinero(s["precio_unitario"]) for s in servicios) if servicios else decimal.Decimal("0")
    subtotal_repuestos = sum(dinero(r["cantidad"]) * dinero(r["precio_unitario"]) for r in repuestos) if repuestos else decimal.Decimal("0")
    subtotal = subtotal_servicios + subtotal_repuestos
    pagado   = sum(dinero(a["monto"]) for a in abonos) if abonos else decimal.Decimal("0")
    iva      = (subtotal - pagado) * _iva_pct() if subtotal > pagado else decimal.Decimal("0")
    total    = (subtotal - pagado) + iva

    return render_template(
        "facturacion_emitir.html",
        ot=ot,
        servicios=servicios, repuestos=repuestos,
        subtotal_servicios=subtotal_servicios, subtotal_repuestos=subtotal_repuestos,
        abonos=abonos, subtotal=subtotal, pagado=pagado, iva=iva, total=total,
        iva_pct=int(_iva_pct()*100),
        cat_serv=cat_serv, cat_rep=cat_rep,
        no_servicios=(len(servicios)==0), no_repuestos=(len(repuestos)==0), no_abonos=(len(abonos)==0)
    )

@bp.post("/agregar-servicio/<int:id_orden>")
@login_required
@roles_required("administrador","facturador")
def agregar_servicio(id_orden:int):
    srv_id  = request.form.get("srv_id", type=int)
    desc    = (request.form.get("srv_desc") or "").strip()
    cant    = request.form.get("srv_cant", type=float) or 1.0
    precio  = request.form.get("srv_precio", type=float) or 0.0

    # si viene id, pero sin desc/precio, tomar del catálogo
    if srv_id and (not desc or precio <= 0):
        cn = get_conn(); ci = cn.cursor()
        try:
            cols = _cols(ci, "cat_servicio")
            label = _first(cols, ["descripcion","nombre"]) or next(iter(cols))
            price = _first(cols, ["precio_base","precio_ref","precio_unitario","precio","valor","monto"])
            if price:
                ci.execute(f"SELECT {label}, {price} FROM cat_servicio WHERE id_servicio=%s", (srv_id,))
            else:
                ci.execute(f"SELECT {label}, 0 FROM cat_servicio WHERE id_servicio=%s", (srv_id,))
            r = ci.fetchone()
            if r:
                if not desc:   desc = r[0]
                if precio<=0:  precio = float(r[1] or 0)
        finally:
            ci.close(); cn.close()

    if not desc:
        flash("Selecciona o escribe un servicio.", "warning")
        return redirect(url_for("facturacion.emitir_form", id_orden=id_orden))

    cn = get_conn(); cur = cn.cursor()
    try:
        cur.execute("""
            INSERT INTO detalle_servicio (id_orden, descripcion, cantidad, precio_unitario)
            VALUES (%s, %s, %s, %s)
        """, (id_orden, desc, str(cant), str(precio)))
        cn.commit()
        flash("Servicio agregado.", "success")
    except Error as e:
        cn.rollback()
        flash(f"No se pudo agregar el servicio: {e}", "danger")
    finally:
        cur.close(); cn.close()
    return redirect(url_for("facturacion.emitir_form", id_orden=id_orden))

@bp.post("/agregar-repuesto/<int:id_orden>")
@login_required
@roles_required("administrador","facturador")
def agregar_repuesto(id_orden:int):
    rep_id  = request.form.get("rep_id", type=int)
    desc    = (request.form.get("rep_desc") or "").strip()
    cant    = request.form.get("rep_cant", type=float) or 1.0
    precio  = request.form.get("rep_precio", type=float) or 0.0

    if rep_id and (not desc or precio <= 0):
        cn = get_conn(); ci = cn.cursor()
        try:
            cols = _cols(ci, "repuesto")
            label = _first(cols, ["descripcion","nombre"]) or next(iter(cols))
            price = _first(cols, ["precio_unitario","precio_base","precio","valor","monto"])
            if price:
                ci.execute(f"SELECT {label}, {price} FROM repuesto WHERE id_repuesto=%s", (rep_id,))
            else:
                ci.execute(f"SELECT {label}, 0 FROM repuesto WHERE id_repuesto=%s", (rep_id,))
            r = ci.fetchone()
            if r:
                if not desc:   desc = r[0]
                if precio<=0:  precio = float(r[1] or 0)
        finally:
            ci.close(); cn.close()

    if not desc:
        flash("Selecciona o escribe un repuesto.", "warning")
        return redirect(url_for("facturacion.emitir_form", id_orden=id_orden))

    cn = get_conn(); cur = cn.cursor()
    try:
        cur.execute("""
            INSERT INTO detalle_repuesto (id_orden, descripcion, cantidad, precio_unitario)
            VALUES (%s, %s, %s, %s)
        """, (id_orden, desc, str(cant), str(precio)))
        cn.commit()
        flash("Repuesto agregado.", "success")
    except Error as e:
        cn.rollback()
        flash(f"No se pudo agregar el repuesto: {e}", "danger")
    finally:
        cur.close(); cn.close()
    return redirect(url_for("facturacion.emitir_form", id_orden=id_orden))

@bp.post("/emitir/<int:id_orden>")
@login_required
@roles_required("administrador", "facturador")
def emitir_post(id_orden: int):
    cn = get_conn(); cur = cn.cursor()
    try:
        cur.execute("""
            SELECT COALESCE(SUM(cantidad * precio_unitario),0)
            FROM (
                SELECT cantidad, precio_unitario FROM detalle_servicio WHERE id_orden=%s
                UNION ALL
                SELECT cantidad, precio_unitario FROM detalle_repuesto WHERE id_orden=%s
            ) x
        """, (id_orden, id_orden))
        subtotal = decimal.Decimal(str(cur.fetchone()[0] or 0))
        cur.execute("SELECT COALESCE(SUM(monto),0) FROM abono WHERE id_orden=%s", (id_orden,))
        pagado = decimal.Decimal(str(cur.fetchone()[0] or 0))

        iva = (subtotal - pagado) * _iva_pct() if subtotal > pagado else decimal.Decimal("0")
        total = (subtotal - pagado) + iva

        cur.execute("""
            INSERT INTO comprobante (id_orden, tipo, subtotal, iva, total, creado_por)
            VALUES (%s, 'FACTURA', %s, %s, %s, %s)
        """, (id_orden, str(subtotal - pagado), str(iva), str(total), int(current_user.id)))
        id_comp = cur.lastrowid

        try: cur.execute("UPDATE orden_trabajo SET estado=%s WHERE id_orden=%s", ("FACTURADA", id_orden))
        except Error: pass

        try:
            cur.execute("""
                INSERT INTO mov_caja (tipo, monto, motivo, id_orden, id_comprobante, creado_por)
                VALUES ('INGRESO', %s, %s, %s, %s, %s)
            """, (str(total), f"Factura OT #{id_orden}", id_orden, id_comp, int(current_user.id)))
        except Error: pass

        cn.commit()
        flash(f"Factura emitida (Comprobante #{id_comp}).", "success")
        return redirect(url_for("facturacion.imprimir", id_comprobante=id_comp))

    except Error as e:
        cn.rollback()
        flash(f"No se pudo emitir la factura: {e}", "danger")
        return redirect(url_for("facturacion.emitir_form", id_orden=id_orden))
    finally:
        try: cur.close(); cn.close()
        except Exception: pass

@bp.get("/imprimir/<int:id_comprobante>")
@login_required
@roles_required("administrador", "facturador")
def imprimir(id_comprobante: int):
    cn = get_conn(); cur = cn.cursor(dictionary=True)
    cur.execute("""
        SELECT comp.id_comprobante, comp.tipo, comp.subtotal, comp.iva, comp.total, comp.creado_en,
               o.id_orden, CONCAT(c.nombres,' ',COALESCE(c.apellidos,'')) AS cli_nombre,
               COALESCE(c.identificacion, c.cedula) AS cli_identificacion, c.telefono AS cli_tel, c.email AS cli_email
        FROM comprobante comp
        JOIN orden_trabajo o ON o.id_orden = comp.id_orden
        JOIN cliente c ON c.id_cliente = o.id_cliente
        WHERE comp.id_comprobante=%s
    """, (id_comprobante,))
    data = cur.fetchone()
    if not data:
        cur.close(); cn.close()
        flash("No existe el comprobante.", "warning")
        return redirect(url_for("index"))

    cur.execute("""
        SELECT 'SERVICIO' AS tipo, descripcion, cantidad, precio_unitario
        FROM detalle_servicio WHERE id_orden=%s
        UNION ALL
        SELECT 'REPUESTO' AS tipo, descripcion, cantidad, precio_unitario
        FROM detalle_repuesto WHERE id_orden=%s
    """, (data["id_orden"], data["id_orden"]))
    items = cur.fetchall()

    cur.close(); cn.close()
    return render_template("facturacion_imprimir.html", data=data, items=items, iva_pct=int(_iva_pct()*100))
