# -*- coding: utf-8 -*-
"""
Local dev DB wipe for 'hifi-vintage' — raw SQL with TRUNCATE CASCADE.

Rationale: ORM unlink() chokes on FK cascades and poisons the Postgres
transaction on first failure. Raw TRUNCATE ... CASCADE is atomic and
lets Postgres figure out the full cascade graph in one shot.

WIPE  : repair.order, repair.batch, repair.pickup.appointment (+ closure),
        sale.order (+ lines), account.move (+ lines),
        stock.picking / stock.move / stock.move.line / stock.quant / stock.lot,
        product.product / product.template,
        repair.device.brand / repair.device.variant,
        orphan mail_message / mail_activity / mail_followers for wiped models
KEEP  : res.partner, res.users, res.groups, res.company, hr.employee,
        product.category, repair.pickup.location, repair.pickup.schedule,
        repair.tags, repair.notes.template, repair.template.* , repair.invoice.template,
        atelier.dashboard.tile, ir.* , mail.template, mail.activity.type,
        stock.warehouse, stock.location

Usage (inside `./odoo-bin shell -c ../odoo.conf -d hifi-vintage --no-http`):
    env.cr.rollback()  # if the previous cell left a poisoned tx
    exec(open('/Users/martin/Documents/odoo_dev/custom_addons/scripts/dev_wipe_transactional.py').read())
"""
import logging
_logger = logging.getLogger("dev_wipe")


def _log(msg):
    _logger.warning("[dev_wipe] " + msg)
    print("[dev_wipe] " + msg)


WIPED_MODELS = [
    "repair.order", "repair.batch",
    "repair.pickup.appointment", "repair.pickup.closure",
    "sale.order", "sale.order.line",
    "account.move", "account.move.line",
    "stock.picking", "stock.move", "stock.move.line", "stock.quant", "stock.lot",
    "product.template", "product.product",
    "repair.device.brand", "repair.device.variant",
]

# Tables to truncate. Order doesn't matter with CASCADE, but listing both parents
# and any extras we want to be explicit about.
TRUNCATE_TABLES = [
    "repair_order", "repair_batch",
    "repair_pickup_appointment", "repair_pickup_closure",
    "sale_order", "sale_order_line",
    "account_move", "account_move_line",
    "stock_picking", "stock_move", "stock_move_line", "stock_quant", "stock_lot",
    "product_template", "product_product",
    "repair_device_brand", "repair_device_variant",
]


def wipe(env):
    _log("=== START (raw SQL TRUNCATE CASCADE) ===")
    cr = env.cr

    # Make sure we start from a clean transaction
    cr.rollback()

    # Filter to tables that actually exist (module might not be installed)
    cr.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_name = ANY(%s)
    """, (TRUNCATE_TABLES,))
    existing = [r[0] for r in cr.fetchall()]
    missing = set(TRUNCATE_TABLES) - set(existing)
    if missing:
        _log(f"tables not in DB (skipped): {sorted(missing)}")
    if not existing:
        _log("nothing to truncate")
        return

    _log(f"truncating {len(existing)} tables with CASCADE: {existing}")
    quoted = ", ".join(f'"{t}"' for t in existing)
    cr.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
    _log("TRUNCATE complete")

    # Orphan chatter/activity/followers referencing wiped models
    cr.execute("""
        DELETE FROM mail_message WHERE model = ANY(%s)
    """, (WIPED_MODELS,))
    _log(f"mail_message: deleted {cr.rowcount} orphan rows")

    cr.execute("""
        DELETE FROM mail_activity WHERE res_model = ANY(%s)
    """, (WIPED_MODELS,))
    _log(f"mail_activity: deleted {cr.rowcount} orphan rows")

    cr.execute("""
        DELETE FROM mail_followers WHERE res_model = ANY(%s)
    """, (WIPED_MODELS,))
    _log(f"mail_followers: deleted {cr.rowcount} orphan rows")

    # Orphan ir_attachment pointing at wiped models
    cr.execute("""
        DELETE FROM ir_attachment WHERE res_model = ANY(%s)
    """, (WIPED_MODELS,))
    _log(f"ir_attachment: deleted {cr.rowcount} orphan rows")

    cr.commit()
    _log("=== DONE (committed) ===")


# When run via `odoo-bin shell` exec, `env` is injected
try:
    wipe(env)  # noqa: F821
except NameError:
    print("Run inside `odoo-bin shell -d hifi-vintage`:")
    print("  env.cr.rollback()")
    print("  exec(open('/Users/martin/Documents/odoo_dev/custom_addons/scripts/dev_wipe_transactional.py').read())")
