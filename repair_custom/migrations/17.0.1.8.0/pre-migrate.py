# -*- coding: utf-8 -*-
"""Audit divergences between cached serial_number and lot.name before drop."""
import logging
_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT ro.id, ro.name, ro.serial_number, sl.name AS lot_name
        FROM repair_order ro
        LEFT JOIN stock_lot sl ON sl.id = ro.lot_id
        WHERE ro.serial_number IS NOT NULL
          AND ro.serial_number != ''
          AND ro.lot_id IS NOT NULL
          AND ro.serial_number != COALESCE(sl.name, '')
    """)
    rows = cr.fetchall()
    if rows:
        _logger.warning(
            "pre-migrate 17.0.1.8.0: %d repair orders have serial_number "
            "diverging from lot.name. Truth will be lot.name after migration.",
            len(rows),
        )
        for ro_id, ro_name, sn, lot_name in rows[:20]:
            _logger.warning("  repair id=%s ref=%s sn=%r lot_name=%r",
                            ro_id, ro_name, sn, lot_name)
