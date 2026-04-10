"""Pre-migration for repair_devices 2.5.

Before Odoo recomputes is_hifi_device (now a stored computed field driven by
categ_id), we set categ_id on all HiFi products so the recomputation yields True.

Handles both upgrade paths:
  - From old schema (no is_hifi_device column): uses repair_device table
  - From 2.x schema (is_hifi_device exists): uses the column directly
"""
import logging

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        )
    """, [table, column])
    return cr.fetchone()[0]


def _table_exists(cr, table):
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables WHERE table_name = %s
        )
    """, [table])
    return cr.fetchone()[0]


def migrate(cr, version):
    # --- Find or create the HiFi category ---
    cr.execute("""
        SELECT res_id FROM ir_model_data
        WHERE module = 'repair_devices' AND name = 'product_category_hifi'
        LIMIT 1
    """)
    row = cr.fetchone()
    if row:
        hifi_cat_id = row[0]
        _logger.info("Found HiFi category id=%d via xmlid", hifi_cat_id)
    else:
        cr.execute("SELECT id FROM product_category WHERE name IN ('Appareils Hi-Fi', 'HIFI') LIMIT 1")
        row = cr.fetchone()
        if row:
            hifi_cat_id = row[0]
            _logger.info("Found HiFi category by name id=%d", hifi_cat_id)
        else:
            cr.execute("""
                INSERT INTO product_category (name, parent_path, create_uid, write_uid, create_date, write_date)
                VALUES ('HIFI', '', 1, 1, NOW(), NOW())
                RETURNING id
            """)
            hifi_cat_id = cr.fetchone()[0]
            cr.execute(
                "UPDATE product_category SET parent_path = %s WHERE id = %s",
                [f"{hifi_cat_id}/", hifi_cat_id],
            )
            _logger.info("Created HiFi category id=%d", hifi_cat_id)

        # Register the xmlid so data loading reuses this record
        # instead of creating a duplicate
        cr.execute("""
            INSERT INTO ir_model_data (module, name, model, res_id, noupdate, create_uid, write_uid, create_date, write_date)
            VALUES ('repair_devices', 'product_category_hifi', 'product.category', %s, TRUE, 1, 1, NOW(), NOW())
            ON CONFLICT (module, name) DO NOTHING
        """, [hifi_cat_id])
        _logger.info("Registered xmlid repair_devices.product_category_hifi → id=%d", hifi_cat_id)

    # --- Assign HiFi category to HiFi products ---
    if _column_exists(cr, 'product_template', 'is_hifi_device'):
        # Upgrading from 2.x where the column already exists
        cr.execute("""
            UPDATE product_template
            SET categ_id = %s
            WHERE is_hifi_device = TRUE
              AND (categ_id IS DISTINCT FROM %s)
        """, [hifi_cat_id, hifi_cat_id])
        _logger.info("Pre-migration (via is_hifi_device): set categ_id on %d rows", cr.rowcount)
    elif _table_exists(cr, 'repair_device'):
        # Upgrading from old schema: use repair_device table to identify HiFi products
        cr.execute("""
            UPDATE product_template
            SET categ_id = %s
            WHERE id IN (
                SELECT product_tmpl_id FROM repair_device
                WHERE product_tmpl_id IS NOT NULL
            )
              AND (categ_id IS DISTINCT FROM %s)
        """, [hifi_cat_id, hifi_cat_id])
        _logger.info("Pre-migration (via repair_device): set categ_id on %d rows", cr.rowcount)
    else:
        _logger.info("Pre-migration: no HiFi products to migrate (fresh install path).")
