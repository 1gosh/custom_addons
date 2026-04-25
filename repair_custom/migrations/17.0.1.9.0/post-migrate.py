# -*- coding: utf-8 -*-
"""Backfill repair.batch.repair_count for rows that still have NULL.

The 17.0.1.5.0 pre-migration created singleton batches via raw SQL and
relied on the ORM recompute pass to populate `repair_count`, but that
pass never ran for these rows — leaving the stored value NULL, which
renders as 0 in the UI.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE repair_batch b
        SET repair_count = COALESCE(sub.cnt, 0)
        FROM (
            SELECT b.id, COUNT(r.id) FILTER (WHERE r.active) AS cnt
            FROM repair_batch b
            LEFT JOIN repair_order r ON r.batch_id = b.id
            WHERE b.repair_count IS NULL
            GROUP BY b.id
        ) sub
        WHERE b.id = sub.id
    """)
    _logger.info(
        "post-migrate 17.0.1.9.0: backfilled repair_count on %d batches",
        cr.rowcount,
    )
