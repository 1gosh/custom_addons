# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class RepairOrder(models.Model):
    _inherit = 'repair.order'

    review_sms_state = fields.Selection(
        [
            ('none', 'Non applicable'),
            ('pending', 'En attente'),
            ('sent', 'Envoyé'),
            ('cancelled', 'Annulé'),
            ('skipped', 'Ignoré'),
        ],
        string="SMS d'avis Google",
        default='none',
        tracking=True,
        copy=False,
    )
    review_sms_eligible_date = fields.Datetime(
        string="SMS d'avis - date prévue",
        copy=False,
    )
    review_sms_sent_date = fields.Datetime(
        string="SMS d'avis - date d'envoi",
        copy=False,
    )
    review_sms_skip_reason = fields.Char(
        string="SMS d'avis - motif d'omission",
        copy=False,
    )

    SKIP_REASON_NO_PHONE = "Pas de numéro mobile"
    SKIP_REASON_DEDUP = "SMS envoyé récemment au client"

    def _get_review_sms_delay_days(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_custom.review_sms_delay_days', 7))

    def _get_review_sms_dedup_months(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_custom.review_sms_dedup_months', 6))

    def _has_review_sms_phone(self):
        self.ensure_one()
        return bool(self.partner_id.mobile or self.partner_id.phone)

    def _review_sms_recently_sent(self):
        self.ensure_one()
        months = self._get_review_sms_dedup_months()
        cutoff = fields.Datetime.now() - relativedelta(months=months)
        return bool(self.env['repair.order'].search_count([
            ('id', '!=', self.id),
            ('partner_id', '=', self.partner_id.id),
            ('review_sms_sent_date', '>=', cutoff),
        ]))

    def _schedule_review_sms(self):
        """Called when delivery_state flips to 'delivered'. Decides
        whether to enqueue, skip, or do nothing."""
        for rec in self:
            if rec.review_sms_state not in ('none', 'cancelled'):
                continue
            if not rec._has_review_sms_phone():
                rec.write({
                    'review_sms_state': 'skipped',
                    'review_sms_skip_reason': rec.SKIP_REASON_NO_PHONE,
                })
                continue
            if rec._review_sms_recently_sent():
                rec.write({
                    'review_sms_state': 'skipped',
                    'review_sms_skip_reason': rec.SKIP_REASON_DEDUP,
                })
                continue
            eligible = fields.Datetime.now() + relativedelta(
                days=rec._get_review_sms_delay_days())
            rec.write({
                'review_sms_state': 'pending',
                'review_sms_eligible_date': eligible,
                'review_sms_skip_reason': False,
            })

    def write(self, vals):
        if 'delivery_state' not in vals:
            return super().write(vals)
        # Snapshot per-record previous state so we can detect transitions
        prev = {rec.id: rec.delivery_state for rec in self}
        res = super().write(vals)
        new_state = vals['delivery_state']
        for rec in self:
            old_state = prev.get(rec.id)
            if old_state != 'delivered' and new_state == 'delivered':
                rec._schedule_review_sms()
            elif old_state == 'delivered' and new_state != 'delivered' \
                    and rec.review_sms_state == 'pending':
                rec.write({
                    'review_sms_state': 'none',
                    'review_sms_eligible_date': False,
                })
        return res
