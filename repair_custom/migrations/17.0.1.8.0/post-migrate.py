# -*- coding: utf-8 -*-
"""Backfill lot_id for any draft repair that still has only serial_number."""
import logging
_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Check column still exists (pre module field removal in the same release)
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='repair_order' AND column_name='serial_number'
    """)
    if not cr.fetchone():
        _logger.info("post-migrate 17.0.1.8.0: serial_number column already dropped, skipping")
        return

    # 1. Match existing lots by (product, serial) where lot_id is empty
    cr.execute("""
        UPDATE repair_order ro
        SET lot_id = sl.id
        FROM stock_lot sl, product_product pp
        WHERE ro.lot_id IS NULL
          AND ro.serial_number IS NOT NULL
          AND ro.serial_number != ''
          AND ro.product_tmpl_id IS NOT NULL
          AND pp.product_tmpl_id = ro.product_tmpl_id
          AND sl.product_id = pp.id
          AND sl.name = ro.serial_number
    """)
    _logger.info("post-migrate 17.0.1.8.0: linked %d repairs to existing lots", cr.rowcount)

    # 2. Create lots for draft repairs with a serial_number but no matching lot
    cr.execute("""
        SELECT ro.id, ro.serial_number, ro.product_tmpl_id, ro.company_id,
               ro.partner_id, ro.variant_id, pp.id AS product_id
        FROM repair_order ro
        JOIN product_product pp ON pp.product_tmpl_id = ro.product_tmpl_id
        WHERE ro.lot_id IS NULL
          AND ro.serial_number IS NOT NULL
          AND ro.serial_number != ''
          AND ro.state = 'draft'
    """)
    rows = cr.fetchall()
    created = 0
    for ro_id, sn, _tmpl, company_id, partner_id, variant_id, product_id in rows:
        cr.execute("""
            INSERT INTO stock_lot (name, product_id, company_id, hifi_partner_id,
                                   hifi_variant_id, is_hifi_unit,
                                   create_date, write_date, create_uid, write_uid)
            VALUES (%s, %s, %s, %s, %s, TRUE, now(), now(), 1, 1)
            RETURNING id
        """, (sn, product_id, company_id, partner_id, variant_id))
        new_lot_id = cr.fetchone()[0]
        cr.execute("UPDATE repair_order SET lot_id = %s WHERE id = %s", (new_lot_id, ro_id))
        created += 1
    _logger.info("post-migrate 17.0.1.8.0: created %d new lots for draft repairs", created)
