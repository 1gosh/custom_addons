"""Pre-migration for repair_custom 17.0.1.4.0.

The `quote_state` field drops the unused 'draft' selection. Odoo refuses
to remove a selection value that has rows in the database, so we
pre-emptively reset any 'draft' rows to 'none' before the schema update.

In practice zero rows are expected — 'draft' was never written by the
previous code. This script is a safety net.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE repair_order
        SET quote_state = 'none'
        WHERE quote_state = 'draft'
    """)
    if cr.rowcount:
        _logger.warning(
            "17.0.1.4.0 pre-migrate: reset %d repair_order rows from 'draft' to 'none'",
            cr.rowcount,
        )
    else:
        _logger.info("17.0.1.4.0 pre-migrate: no 'draft' rows to migrate")
