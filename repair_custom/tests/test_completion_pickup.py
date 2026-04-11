# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import RepairQuoteCase


@tagged('post_install', '-at_install', 'repair_completion_pickup')
class TestMandatoryBatches(RepairQuoteCase):

    def test_create_repair_auto_wraps_batch(self):
        """A repair created without explicit batch_id gets a singleton batch."""
        repair = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'auto-wrap test',
        })
        self.assertTrue(repair.batch_id, "batch_id should be populated on create")
        self.assertEqual(repair.batch_id.partner_id, self.partner)
        self.assertEqual(repair.batch_id.repair_ids, repair)
