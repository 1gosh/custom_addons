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
