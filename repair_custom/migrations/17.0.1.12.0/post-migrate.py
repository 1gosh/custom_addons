# -*- coding: utf-8 -*-
"""Post-migration for 17.0.1.12.0.

Introduces the intake fee ("Prise en charge / Diagnostic"). Existing repair
orders must not suddenly show up as "fee not collected": stamp
`repair_custom.intake_fee_start_date` to today so `repair.order.intake_fee_state`
(computed, see models/repair_order.py) treats every repair dropped off before
this upgrade as out of scope. Skipped on a fresh install (no existing repairs,
nothing to protect) or if the parameter was already set by a prior run.
"""
import logging

from odoo import api, fields, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()

    if ICP.get_param('repair_custom.intake_fee_start_date'):
        return

    cr.execute("SELECT COUNT(*) FROM repair_order")
    (existing_count,) = cr.fetchone()
    if not existing_count:
        return

    today = fields.Date.context_today(env.user)
    ICP.set_param('repair_custom.intake_fee_start_date', today)
    _logger.info(
        "post-migrate 17.0.1.12.0: set repair_custom.intake_fee_start_date=%s "
        "for %d existing repair order(s)",
        today, existing_count,
    )
