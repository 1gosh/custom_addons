from . import models
from . import wizard

from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)


def _post_init_migrate_devices(env):
    """Migrate repair.device → product.template and repair.device.unit → stock.lot.

    This runs on module update (-u). It uses raw SQL to copy data from the old
    custom tables into the extended native Odoo tables.
    Old tables are kept as orphans for safety — drop manually after validation.
    """
    cr = env.cr

    # Check if old tables exist (they won't on fresh install)
    cr.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'repair_device')")
    if not cr.fetchone()[0]:
        _logger.info("No repair_device table found — skipping migration (fresh install).")
        return

    _logger.info("Starting migration: repair.device → product.template ...")

    # --- 4a. Migrate repair.device → product.template ---
    cr.execute("""
        UPDATE product_template
        SET is_hifi_device = TRUE,
            brand_id = rd.brand_id,
            production_year = rd.production_year
        FROM repair_device rd
        WHERE product_template.id = rd.product_tmpl_id
          AND rd.product_tmpl_id IS NOT NULL
    """)
    _logger.info("Migrated repair.device fields to product.template: %d rows", cr.rowcount)

    # Assign HiFi category so computed is_hifi_device stays True after recompute
    hifi_cat = env.ref('repair_devices.product_category_hifi', raise_if_not_found=False)
    if hifi_cat:
        cr.execute("""
            UPDATE product_template
            SET categ_id = %s
            WHERE is_hifi_device = TRUE
              AND (categ_id IS DISTINCT FROM %s)
        """, [hifi_cat.id, hifi_cat.id])
        _logger.info("Assigned HiFi category to %d products", cr.rowcount)

    # Migrate M2M variants (old table: repair_device_variant_rel with device_id/variant_id)
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'repair_device_variant_rel'
        )
    """)
    if cr.fetchone()[0]:
        cr.execute("""
            INSERT INTO product_template_variant_rel (product_template_id, repair_device_variant_id)
            SELECT rd.product_tmpl_id, rdvr.variant_id
            FROM repair_device_variant_rel rdvr
            JOIN repair_device rd ON rd.id = rdvr.device_id
            WHERE rd.product_tmpl_id IS NOT NULL
            ON CONFLICT DO NOTHING
        """)
        _logger.info("Migrated variant M2M: %d rows", cr.rowcount)

    # Ensure all migrated products have correct tracking/type
    cr.execute("""
        UPDATE product_template
        SET tracking = 'serial', type = 'product', sale_ok = TRUE
        WHERE is_hifi_device = TRUE
          AND (tracking != 'serial' OR type != 'product' OR sale_ok != TRUE)
    """)

    # --- 4b. Migrate repair.device.unit → stock.lot ---
    cr.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'repair_device_unit')")
    if not cr.fetchone()[0]:
        _logger.info("No repair_device_unit table found — skipping unit migration.")
        return

    _logger.info("Starting migration: repair.device.unit → stock.lot ...")

    # Check if stock_state, sale_date etc. columns exist on stock_lot (added by repair_custom)
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'stock_lot' AND column_name = 'is_hifi_unit'
    """)
    lot_has_hifi_fields = bool(cr.fetchone())

    if not lot_has_hifi_fields:
        _logger.info("stock.lot HiFi fields not yet created — deferring unit migration to repair_custom.")
        return

    # Units WITH existing lot_id — copy fields to lot
    cr.execute("""
        UPDATE stock_lot sl
        SET is_hifi_unit = TRUE,
            hifi_partner_id = rdu.partner_id,
            hifi_notes = rdu.notes,
            hifi_variant_id = rdu.variant_id
        FROM repair_device_unit rdu
        WHERE rdu.lot_id = sl.id
          AND rdu.lot_id IS NOT NULL
    """)
    _logger.info("Migrated units with lot_id: %d rows", cr.rowcount)

    # Units WITHOUT lot_id — create stock.lot records
    cr.execute("""
        SELECT rdu.id, rdu.serial_number, rdu.partner_id, rdu.notes,
               rdu.variant_id,
               pp.id as product_id, rd.product_tmpl_id,
               (SELECT id FROM res_company LIMIT 1) as company_id
        FROM repair_device_unit rdu
        JOIN repair_device rd ON rd.id = rdu.device_id
        JOIN product_template pt ON pt.id = rd.product_tmpl_id
        JOIN product_product pp ON pp.product_tmpl_id = pt.id
        WHERE rdu.lot_id IS NULL
          AND rd.product_tmpl_id IS NOT NULL
    """)
    units_no_lot = cr.dictfetchall()
    _logger.info("Found %d units without lot_id — creating stock.lot records...", len(units_no_lot))

    for unit in units_no_lot:
        serial = unit['serial_number'] or f"UNIT-{unit['id']}"
        cr.execute("""
            INSERT INTO stock_lot (name, product_id, company_id, is_hifi_unit,
                hifi_partner_id, hifi_notes, hifi_variant_id,
                create_uid, create_date, write_uid, write_date)
            VALUES (%s, %s, %s, TRUE, %s, %s, %s,
                    1, NOW(), 1, NOW())
            RETURNING id
        """, (serial, unit['product_id'], unit['company_id'],
              unit['partner_id'], unit['notes'],
              unit['variant_id']))
        lot_id = cr.fetchone()[0]
        # Store mapping for repair.order migration
        cr.execute("""
            UPDATE repair_device_unit SET lot_id = %s WHERE id = %s
        """, (lot_id, unit['id']))

    # --- 4c. Migrate repair.order references ---
    # Match repair orders to lots via serial_number = stock_lot.name
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'repair_order' AND column_name IN ('lot_id', 'product_tmpl_id')
    """)
    ro_columns = {row[0] for row in cr.fetchall()}
    if 'lot_id' in ro_columns and 'product_tmpl_id' in ro_columns:
        cr.execute("""
            UPDATE repair_order ro
            SET lot_id = sl.id,
                product_tmpl_id = COALESCE(ro.product_tmpl_id, pp.product_tmpl_id)
            FROM stock_lot sl
            JOIN product_product pp ON pp.id = sl.product_id
            WHERE sl.name = ro.serial_number
              AND sl.is_hifi_unit = TRUE
              AND ro.lot_id IS NULL
              AND ro.serial_number IS NOT NULL
        """)
        _logger.info("Migrated repair.order lot_id (via serial_number match): %d rows", cr.rowcount)
    elif 'lot_id' in ro_columns:
        # product_tmpl_id not yet available (repair_custom not updated yet) — skip,
        # repair_custom 17.0.1.2.5 post-migrate will handle this.
        _logger.info("repair_order.product_tmpl_id not yet available — deferring to repair_custom migration.")

    # --- 4d. Migrate sale.order.line references ---
    # Match SOL to lot via repair_device_unit.sale_order_id
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'sale_order_line' AND column_name = 'lot_id'
    """)
    if cr.fetchone():
        cr.execute("""
            UPDATE sale_order_line sol
            SET lot_id = rdu.lot_id
            FROM repair_device_unit rdu
            WHERE rdu.sale_order_id = sol.order_id
              AND sol.lot_id IS NULL
              AND rdu.lot_id IS NOT NULL
        """)
        _logger.info("Migrated sale.order.line lot_id (via sale_order match): %d rows", cr.rowcount)

    # --- 4e. Migrate stock_state and warranty fields to stock.lot ---
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'stock_lot' AND column_name = 'stock_state'
    """)
    if cr.fetchone():
        cr.execute("""
            UPDATE stock_lot sl
            SET stock_state = rdu.stock_state,
                sale_date = rdu.sale_date,
                sav_expiry = rdu.sav_expiry,
                sale_order_id = rdu.sale_order_id,
                last_delivered_repair_id = rdu.last_delivered_repair_id,
                sar_expiry = rdu.sar_expiry
            FROM repair_device_unit rdu
            WHERE rdu.lot_id = sl.id
              AND sl.is_hifi_unit = TRUE
        """)
        _logger.info("Migrated warranty/stock_state fields: %d rows", cr.rowcount)

    _logger.info("Migration complete. Old tables kept as orphans.")
