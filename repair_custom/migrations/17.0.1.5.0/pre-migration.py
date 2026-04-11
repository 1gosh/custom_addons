# -*- coding: utf-8 -*-
"""Backfill singleton batches for batchless repair.order rows.

Runs before schema update so the new `batch_id` NOT NULL constraint
finds no violating rows.

Uses raw SQL only — the ORM registry is not available during pre-migration.
"""
from odoo import fields


def _next_sequence(cr, code):
    """Fetch next value from ir.sequence by code, using raw SQL.

    For sequences with implementation='standard' (the default), Odoo stores
    the real counter in a PostgreSQL sequence object named ir_sequence_NNN
    (zero-padded 3-digit id).  The number_next column is only a display mirror
    and must NOT be used directly.  We call nextval() on the PG sequence so the
    counter advances correctly and there is no collision with the first
    ORM-created batch after upgrade.

    For implementation='no_gap', Odoo uses number_next with row-level locking;
    we replicate that path by bumping the column directly.

    Returns a formatted string like '001' or None if the sequence is missing.
    """
    cr.execute(
        "SELECT id, prefix, suffix, padding, number_increment, implementation "
        "FROM ir_sequence WHERE code = %s AND active = TRUE LIMIT 1",
        (code,),
    )
    row = cr.fetchone()
    if not row:
        return None
    seq_id, prefix, suffix, padding, number_increment, implementation = row

    if implementation == 'no_gap':
        # no_gap: read number_next, then bump the column
        cr.execute("SELECT number_next FROM ir_sequence WHERE id = %s", (seq_id,))
        number_next = cr.fetchone()[0]
        cr.execute(
            "UPDATE ir_sequence SET number_next = number_next + %s WHERE id = %s",
            (number_increment, seq_id),
        )
    else:
        # standard: advance the real PostgreSQL sequence object
        pg_seq_name = f"ir_sequence_{seq_id:03d}"
        cr.execute("SELECT nextval(%s)", (pg_seq_name,))
        number_next = cr.fetchone()[0]

    prefix = prefix or ''
    suffix = suffix or ''
    return f"{prefix}{str(number_next).zfill(padding)}{suffix}"


def migrate(cr, version):
    cr.execute(
        "SELECT id, partner_id, entry_date, company_id "
        "FROM repair_order WHERE batch_id IS NULL"
    )
    rows = cr.fetchall()
    now = fields.Datetime.now()

    for repair_id, partner_id, entry_date, company_id in rows:
        if not partner_id:
            # No partner = cannot batch meaningfully; skip.
            continue

        # Build a name prefix from the partner name (mirrors RepairBatch.create logic)
        cr.execute("SELECT name FROM res_partner WHERE id = %s", (partner_id,))
        partner_row = cr.fetchone()
        partner_name = partner_row[0] if partner_row else ''
        prefix = ''
        if partner_name:
            prefix = partner_name.upper().replace(' ', '').replace('.', '')[:4] + '-'

        seq_val = _next_sequence(cr, 'repair.batch') or '00000'
        batch_name = f"{prefix}{seq_val}"

        batch_date = entry_date or now

        # repair_count and state are store=True computed fields; omit them here
        # so PostgreSQL uses column defaults — the ORM recompute pass after
        # upgrade will populate them correctly.
        cr.execute(
            "INSERT INTO repair_batch (name, date, partner_id, company_id) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (batch_name, batch_date, partner_id, company_id),
        )
        batch_id = cr.fetchone()[0]

        cr.execute(
            "UPDATE repair_order SET batch_id = %s WHERE id = %s",
            (batch_id, repair_id),
        )
