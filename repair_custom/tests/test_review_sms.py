# -*- coding: utf-8 -*-
from unittest.mock import patch
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'review_sms')
class ReviewSmsCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Repair = cls.env['repair.order']
        cls.Partner = cls.env['res.partner']

        cls.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.review_sms_delay_days', '7')
        cls.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.review_sms_dedup_months', '6')

        cls.partner_with_mobile = cls.Partner.create({
            'name': 'Client Avec Mobile',
            'mobile': '+33611112222',
        })
        cls.partner_without_phone = cls.Partner.create({
            'name': 'Client Sans Téléphone',
        })

        # Make sure default template is referenced in config
        template = cls.env.ref('repair_custom.sms_template_review_request')
        cls.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.review_sms_template_id', str(template.id))
        cls.template = template

    def _make_delivered_repair(self, partner=None):
        """Create a repair and force-set it to delivered for testing."""
        partner = partner or self.partner_with_mobile
        repair = self.Repair.create({'partner_id': partner.id})
        # Bypass workflow: directly write delivery_state to trigger our hook
        repair.write({'delivery_state': 'delivered'})
        return repair

    def test_pending_on_delivery_with_mobile(self):
        repair = self._make_delivered_repair()
        self.assertEqual(repair.review_sms_state, 'pending')
        self.assertTrue(repair.review_sms_eligible_date)
        # eligible date roughly = now + 7 days
        delta = repair.review_sms_eligible_date - fields.Datetime.now()
        self.assertGreater(delta.total_seconds(), 6 * 86400)
        self.assertLess(delta.total_seconds(), 8 * 86400)

    def test_skipped_on_delivery_without_phone(self):
        repair = self._make_delivered_repair(partner=self.partner_without_phone)
        self.assertEqual(repair.review_sms_state, 'skipped')
        self.assertEqual(repair.review_sms_skip_reason, "Pas de numéro mobile")

    def test_skipped_on_delivery_when_dedup_window_matches(self):
        # First repair already sent recently
        prior = self._make_delivered_repair()
        prior.write({
            'review_sms_state': 'sent',
            'review_sms_sent_date': fields.Datetime.now() - relativedelta(months=1),
        })
        # New repair for same partner
        new_repair = self.Repair.create({'partner_id': self.partner_with_mobile.id})
        new_repair.write({'delivery_state': 'delivered'})
        self.assertEqual(new_repair.review_sms_state, 'skipped')
        self.assertEqual(new_repair.review_sms_skip_reason, "SMS envoyé récemment au client")

    def test_revert_delivery_resets_pending(self):
        repair = self._make_delivered_repair()
        self.assertEqual(repair.review_sms_state, 'pending')
        repair.write({'delivery_state': 'none'})
        self.assertEqual(repair.review_sms_state, 'none')

    def test_skipped_when_sibling_pending(self):
        # First delivery → pending
        first = self._make_delivered_repair()
        self.assertEqual(first.review_sms_state, 'pending')
        # Second delivery for same partner before first SMS fires → skipped
        second = self.Repair.create({'partner_id': self.partner_with_mobile.id})
        second.write({'delivery_state': 'delivered'})
        self.assertEqual(second.review_sms_state, 'skipped')
        self.assertEqual(second.review_sms_skip_reason, "SMS envoyé récemment au client")

    def _force_eligible(self, repair, when=None):
        repair.write({
            'review_sms_eligible_date': when or (fields.Datetime.now() - relativedelta(minutes=1)),
        })

    def test_cron_skips_not_yet_eligible(self):
        repair = self._make_delivered_repair()
        repair.write({
            'review_sms_eligible_date': fields.Datetime.now() + relativedelta(days=1),
        })
        with patch.object(type(self.template), 'send_sms') as mock_send:
            self.Repair._cron_send_review_sms()
            mock_send.assert_not_called()
        self.assertEqual(repair.review_sms_state, 'pending')

    def test_cron_sends_eligible_repair(self):
        repair = self._make_delivered_repair()
        self._force_eligible(repair)
        with patch.object(type(self.template), 'send_sms') as mock_send:
            self.Repair._cron_send_review_sms()
            mock_send.assert_called_once_with(repair.id)
        self.assertEqual(repair.review_sms_state, 'sent')
        self.assertTrue(repair.review_sms_sent_date)

    def test_cron_dedup_recheck_at_send_time(self):
        # Eligible repair, but a sibling got sent in between
        repair = self._make_delivered_repair()
        self._force_eligible(repair)
        sibling = self.Repair.create({'partner_id': self.partner_with_mobile.id})
        sibling.write({
            'delivery_state': 'delivered',
            'review_sms_state': 'sent',
            'review_sms_sent_date': fields.Datetime.now(),
        })
        with patch.object(type(self.template), 'send_sms') as mock_send:
            self.Repair._cron_send_review_sms()
            mock_send.assert_not_called()
        self.assertEqual(repair.review_sms_state, 'skipped')
        self.assertEqual(repair.review_sms_skip_reason, "SMS envoyé récemment au client")

    def test_cron_send_failure_keeps_pending(self):
        repair = self._make_delivered_repair()
        self._force_eligible(repair)
        with patch.object(type(self.template), 'send_sms', side_effect=Exception("IAP fail")):
            self.Repair._cron_send_review_sms()
        self.assertEqual(repair.review_sms_state, 'pending')

    def test_cancel_action_marks_cancelled(self):
        repair = self._make_delivered_repair()
        self.assertEqual(repair.review_sms_state, 'pending')
        repair.action_cancel_review_sms()
        self.assertEqual(repair.review_sms_state, 'cancelled')

    def test_cancel_then_cron_skips(self):
        repair = self._make_delivered_repair()
        self._force_eligible(repair)
        repair.action_cancel_review_sms()
        with patch.object(type(self.template), 'send_sms') as mock_send:
            self.Repair._cron_send_review_sms()
            mock_send.assert_not_called()
        self.assertEqual(repair.review_sms_state, 'cancelled')
