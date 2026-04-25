"""Enforce at most one active (pending/scheduled) pickup appointment per
batch via a partial unique index. Defends against concurrent create races
that the Python @api.constrains cannot catch.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        "ALTER TABLE repair_batch "
        "DROP COLUMN IF EXISTS ready_for_pickup_notification"
    )

    cr.execute("""
        SELECT batch_id
        FROM repair_pickup_appointment
        WHERE state IN ('pending', 'scheduled')
        GROUP BY batch_id
        HAVING COUNT(*) > 1
    """)
    dupes = [row[0] for row in cr.fetchall()]
    if dupes:
        _logger.warning(
            "Cannot create unique index: %d batch(es) already have multiple "
            "active appointments: %s. Resolve manually then rerun.",
            len(dupes), dupes,
        )
        return

    cr.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            repair_pickup_appointment_active_batch_uniq
        ON repair_pickup_appointment (batch_id)
        WHERE state IN ('pending', 'scheduled')
    """)
