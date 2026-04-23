"""Migrate repair.pickup.appointment from datetime-slot to date-only model.

Also migrate repair.pickup.schedule from per-slot capacity to per-day
capacity.

Runs BEFORE Odoo loads the new ORM schema, so we operate in raw SQL.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # --- repair.pickup.appointment: add pickup_date, backfill, drop old cols ---
    cr.execute("""
        ALTER TABLE repair_pickup_appointment
        ADD COLUMN IF NOT EXISTS pickup_date date
    """)

    # Backfill: take the DATE part of start_datetime (in UTC; Odoo stores UTC).
    # The operational timezone is Europe/Paris — convert before casting.
    cr.execute("""
        UPDATE repair_pickup_appointment
           SET pickup_date = (start_datetime AT TIME ZONE 'UTC'
                                              AT TIME ZONE 'Europe/Paris')::date
         WHERE start_datetime IS NOT NULL
           AND pickup_date IS NULL
    """)

    for col in ('start_datetime', 'end_datetime'):
        cr.execute(
            "ALTER TABLE repair_pickup_appointment DROP COLUMN IF EXISTS %s" % col
        )

    # --- repair.pickup.schedule: add daily_capacity, backfill, drop slot cols ---
    cr.execute("""
        ALTER TABLE repair_pickup_schedule
        ADD COLUMN IF NOT EXISTS daily_capacity integer
    """)

    cr.execute("""
        UPDATE repair_pickup_schedule
           SET daily_capacity = COALESCE(slot_capacity, 6)
         WHERE daily_capacity IS NULL
    """)

    for col in ('slot1_start', 'slot1_end', 'slot2_start', 'slot2_end',
                'slot_capacity'):
        cr.execute(
            "ALTER TABLE repair_pickup_schedule DROP COLUMN IF EXISTS %s" % col
        )

    _logger.info("repair_appointment 17.0.1.1.0 migration complete")
