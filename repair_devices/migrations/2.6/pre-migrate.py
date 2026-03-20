"""Pre-migration for repair_devices 2.6.

Fixes the duplicate HiFi category that 2.5 pre-migrate may have created
(raw SQL without xmlid) when the data XML later creates another with the xmlid.
Consolidates all products onto the xmlid-backed category and removes orphans.
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


def migrate(cr, version):
    # Find the xmlid-backed category
    cr.execute("""
        SELECT res_id FROM ir_model_data
        WHERE module = 'repair_devices' AND name = 'product_category_hifi'
    """)
    row = cr.fetchone()
    if not row:
        _logger.info("No xmlid category found — nothing to fix.")
        return
    good_id = row[0]

    # Find orphan categories with same name but different id
    cr.execute("""
        SELECT id FROM product_category
        WHERE name = 'Appareils Hi-Fi' AND id != %s
    """, [good_id])
    orphan_ids = [r[0] for r in cr.fetchall()]

    if orphan_ids:
        _logger.info("Consolidating %d orphan HiFi categories into id=%d", len(orphan_ids), good_id)

        # Move products from orphan categories to the correct one
        cr.execute("""
            UPDATE product_template
            SET categ_id = %s
            WHERE categ_id = ANY(%s)
        """, [good_id, orphan_ids])
        _logger.info("Moved %d products to correct category", cr.rowcount)

        # Delete orphan categories
        cr.execute("DELETE FROM product_category WHERE id = ANY(%s)", [orphan_ids])
        _logger.info("Deleted %d orphan categories", cr.rowcount)

    # Ensure any remaining HiFi products have correct categ_id
    # Only reference is_hifi_device if the column exists (may not on old→2.6 jump)
    if _column_exists(cr, 'product_template', 'is_hifi_device'):
        cr.execute("""
            UPDATE product_template
            SET categ_id = %s
            WHERE is_hifi_device = TRUE AND categ_id != %s
        """, [good_id, good_id])
        if cr.rowcount:
            _logger.info("Fixed categ_id on %d additional HiFi products", cr.rowcount)
