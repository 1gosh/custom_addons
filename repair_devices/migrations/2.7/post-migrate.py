"""Post-migration for repair_devices 2.7.

Recomputes is_hifi_device for all HiFi products, then cleans up
the orphan hifi_category_id column and renames the old category table.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("2.7 post-migrate: recomputing is_hifi_device and cleaning up orphan objects")

    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    hifi_cat = env.ref('repair_devices.product_category_hifi', raise_if_not_found=False)
    if hifi_cat:
        templates = env['product.template'].search([('categ_id', 'child_of', hifi_cat.id)])
        _logger.info("Recomputing is_hifi_device for %d templates", len(templates))
        templates._compute_is_hifi_device()
        templates.flush_recordset()

    # Drop orphan column (the field no longer exists in the model)
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = 'product_template' AND column_name = 'hifi_category_id'
        )
    """)
    if cr.fetchone()[0]:
        cr.execute("ALTER TABLE product_template DROP COLUMN IF EXISTS hifi_category_id")
        _logger.info("Dropped orphan column product_template.hifi_category_id")

    # Archive old table rather than dropping (safe recovery option)
    cr.execute("""
        SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'repair_device_category')
    """)
    if cr.fetchone()[0]:
        cr.execute("ALTER TABLE repair_device_category RENAME TO repair_device_category_archived_2_7")
        _logger.info("Renamed repair_device_category → repair_device_category_archived_2_7")

    _logger.info("2.7 post-migrate: complete.")
