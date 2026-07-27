# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class SmsTemplate(models.Model):
    _inherit = 'sms.template'

    def _send_review_sms(self, res_id):
        """Send this SMS template to the given record id (uses IAP)."""
        self.ensure_one()
        record = self.env[self.model].browse(res_id)
        record._message_sms_with_template(template=self)


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

    def _review_sms_recently_handled(self):
        """True if the partner already has another repair where a review
        SMS has been sent within the dedup window OR is currently
        scheduled (pending). Avoids double-SMS for back-to-back repairs."""
        self.ensure_one()
        months = self._get_review_sms_dedup_months()
        cutoff = fields.Datetime.now() - relativedelta(months=months)
        Repair = self.env['repair.order']
        already_sent = Repair.search_count([
            ('id', '!=', self.id),
            ('partner_id', '=', self.partner_id.id),
            ('review_sms_sent_date', '>=', cutoff),
        ])
        if already_sent:
            return True
        pending = Repair.search_count([
            ('id', '!=', self.id),
            ('partner_id', '=', self.partner_id.id),
            ('review_sms_state', '=', 'pending'),
        ])
        return bool(pending)

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
            if rec._review_sms_recently_handled():
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

    @api.model
    def _get_review_sms_template(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'repair_custom.review_sms_template_id')
        if param:
            template = self.env['sms.template'].browse(int(param)).exists()
            if template:
                return template
        return self.env.ref(
            'repair_custom.sms_template_review_request',
            raise_if_not_found=False)

    @api.model
    def _cron_send_review_sms(self):
        template = self._get_review_sms_template()
        if not template:
            _logger.warning("Review SMS template not configured, skipping cron")
            return
        repairs = self.search([
            ('review_sms_state', '=', 'pending'),
            ('review_sms_eligible_date', '<=', fields.Datetime.now()),
        ])
        for repair in repairs:
            with self.env.cr.savepoint():
                if not repair._has_review_sms_phone():
                    repair.write({
                        'review_sms_state': 'skipped',
                        'review_sms_skip_reason': repair.SKIP_REASON_NO_PHONE,
                    })
                    continue
                if repair._review_sms_recently_handled():
                    repair.write({
                        'review_sms_state': 'skipped',
                        'review_sms_skip_reason': repair.SKIP_REASON_DEDUP,
                    })
                    continue
                try:
                    template._send_review_sms(repair.id)
                    repair.write({
                        'review_sms_state': 'sent',
                        'review_sms_sent_date': fields.Datetime.now(),
                    })
                    repair.message_post(body=_("SMS d'avis Google envoyé."))
                except Exception as e:
                    _logger.exception(
                        "Review SMS send failed for repair %s", repair.id)
                    repair.message_post(
                        body=_("Échec envoi SMS d'avis : %s") % e)

    def action_cancel_review_sms(self):
        for rec in self:
            if rec.review_sms_state != 'pending':
                continue
            rec.write({'review_sms_state': 'cancelled'})
            rec.message_post(body=_("SMS d'avis Google annulé manuellement."))
        return True

    def write(self, vals):
        if 'delivery_state' not in vals:
            return super().write(vals)
        # Inner writes performed by _schedule_review_sms must NEVER include
        # 'delivery_state' — they would recurse into this branch. Keep that
        # invariant if you add new inner writes.
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
