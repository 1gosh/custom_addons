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

    def test_placeholder(self):
        self.assertTrue(self.template)
