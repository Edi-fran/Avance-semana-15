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

@bp.get("/emitir/<int:id_orden>")
@login_required
@roles_required("administrador", "facturador")
def emitir_form(id_orden: int):
    """
    Muestra resumen de la orden: cliente, items (servicios + repuestos), abonos y totales.
    """
    cn = get_conn(); cur = cn.cursor(dictionary=True)

    # ---- Cabecera de la OT + cliente
    cur.execute("""
        SELECT o.id_orden, o.descripcion, o.estado, o.creado_en, o.id_cliente,
               c.id_cliente   AS cli_id, c.nombres AS cli_nombre, c.cedula AS cli_cedula,
               c.telefono AS cli_tel, c.email AS cli_email
        FROM orden_trabajo o
        JOIN cliente c ON c.id_cliente = o.id_cliente
        WHERE o.id_orden=%s
    """, (id_orden,))
    ot = cur.fetchone()
    if not ot:
        cur.close(); cn.close()
        flash("No existe la Orden indicada.", "warning")
        return redirect(url_for("index"))

    # ---- Items (ajusta nombres de columnas si difieren)
    cur.execute("""
        SELECT ds.id_detalle_servicio AS id, 'SERVICIO' AS tipo, ds.descripcion, ds.cantidad, ds.precio_unitario
        FROM detalle_servicio ds
        WHERE ds.id_orden=%s
        UNION ALL
        SELECT dr.id_detalle_repuesto AS id, 'REPUESTO' AS tipo, dr.descripcion, dr.cantidad, dr.precio_unitario
        FROM detalle_repuesto dr
        WHERE dr.id_orden=%s
    """, (id_orden, id_orden))
    items = cur.fetchall()

    # ---- Abonos
    cur.execute("""
        SELECT a.id_abono, a.monto, a.creado_en, u.usuario_login
        FROM abono a
        LEFT JOIN usuario u ON u.id_usuario = a.id_usuario
        WHERE a.id_orden=%s
        ORDER BY a.creado_en
    """, (id_orden,))
    abonos = cur.fetchall()

    cur.close(); cn.close()

    # ---- Totales
    subtotal = sum(decimal.Decimal(str(it["cantidad"])) * decimal.Decimal(str(it["precio_unitario"])) for it in items) if items else decimal.Decimal("0")
    pagado   = sum(decimal.Decimal(str(a["monto"])) for a in abonos) if abonos else decimal.Decimal("0")
    iva      = (subtotal - pagado) * _iva_pct() if subtotal > pagado else decimal.Decimal("0")
    total    = (subtotal - pagado) + iva

    return render_template("facturacion_emitir.html",
                           ot=ot, items=items, abonos=abonos,
                           subtotal=subtotal, pagado=pagado, iva=iva, total=total)

@bp.post("/emitir/<int:id_orden>")
@login_required
@roles_required("administrador", "facturador")
def emitir_post(id_orden: int):
    """
    Inserta la factura (comprobante) y registra el movimiento de caja.
    """
    cn = get_conn(); cur = cn.cursor()

    try:
        # Recalcular totales en BD para seguridad
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

        # Crear comprobante
        cur.execute("""
            INSERT INTO comprobante (id_orden, tipo, subtotal, iva, total, creado_por)
            VALUES (%s, 'FACTURA', %s, %s, %s, %s)
        """, (id_orden, str(subtotal - pagado), str(iva), str(total), int(current_user.id)))
        id_comp = cur.lastrowid

        # Actualizar estado de OT (si tu tabla tiene este campo)
        try:
            cur.execute("UPDATE orden_trabajo SET estado=%s WHERE id_orden=%s", ("FACTURADA", id_orden))
        except Error:
            pass

        # Registrar ingreso en caja (ajusta si tu modelo de caja es distinto)
        try:
            cur.execute("""
                INSERT INTO mov_caja (tipo, monto, motivo, id_orden, id_comprobante, creado_por)
                VALUES ('INGRESO', %s, %s, %s, %s, %s)
            """, (str(total), f"Factura OT #{id_orden}", id_orden, id_comp, int(current_user.id)))
        except Error:
            pass

        cn.commit()
        flash(f"Factura emitida (Comprobante #{id_comp}).", "success")
        return redirect(url_for("facturacion.imprimir", id_comprobante=id_comp))

    except Error as e:
        cn.rollback()
        flash(f"No se pudo emitir la factura: {e}", "danger")
        return redirect(url_for("facturacion.emitir_form", id_orden=id_orden))
    finally:
        try:
            cur.close(); cn.close()
        except Exception:
            pass

@bp.get("/imprimir/<int:id_comprobante>")
@login_required
@roles_required("administrador", "facturador")
def imprimir(id_comprobante: int):
    """
    Vista simple imprimible del comprobante.
    """
    cn = get_conn(); cur = cn.cursor(dictionary=True)

    # Cabecera del comprobante + orden + cliente
    cur.execute("""
        SELECT comp.id_comprobante, comp.tipo, comp.subtotal, comp.iva, comp.total, comp.creado_en,
               o.id_orden, c.nombres AS cli_nombre, c.cedula AS cli_cedula, c.telefono AS cli_tel, c.email AS cli_email
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

    # Items facturados (de la OT) para mostrar en impresión
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
