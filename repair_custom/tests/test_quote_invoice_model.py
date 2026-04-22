# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from .common import RepairQuoteCase


class TestAccountMoveAutoStamp(RepairQuoteCase):
    """account.move.create auto-stamps repair_id/batch_id via sale-line fallback."""

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()
        self.so = self._make_sale_order_linked(self.repair)
        self.so.action_confirm()  # state='sale' so _create_invoices() works

    def test_auto_stamp_on_native_create_invoices(self):
        """Calling sale.order._create_invoices() populates repair_id & batch_id."""
        moves = self.so._create_invoices()
        self.assertEqual(len(moves), 1)
        move = moves
        self.assertEqual(move.repair_id, self.repair,
                         "repair_id should auto-stamp when exactly one repair resolves")
        self.assertEqual(move.batch_id, self.repair.batch_id,
                         "batch_id should auto-stamp when exactly one batch resolves")

    def test_auto_stamp_noop_on_non_out_invoice(self):
        """move_type != 'out_invoice' does not trigger stamping."""
        move = self.env['account.move'].create({
            'move_type': 'out_refund',
            'partner_id': self.partner.id,
        })
        self.assertFalse(move.repair_id)
        self.assertFalse(move.batch_id)

    def test_auto_stamp_idempotent_when_prestamped(self):
        """Pre-existing repair_id is preserved (not overwritten)."""
        other_repair = self._make_repair()
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'repair_id': other_repair.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.service_product.id,
                'name': 'Test',
                'quantity': 1,
                'price_unit': 10.0,
            })],
        })
        self.assertEqual(move.repair_id, other_repair,
                         "Pre-stamped repair_id must survive the create hook")
