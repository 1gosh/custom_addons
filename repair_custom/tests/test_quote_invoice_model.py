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


class TestSectionHeaderInjection(RepairQuoteCase):
    """_inject_repair_section_headers prepends a line_section per source SO."""

    def setUp(self):
        super().setUp()
        # Two repairs in one batch, each with its own sale.order
        self.repair_a = self._make_repair(internal_notes='Diag A')
        self.repair_a.serial_number = 'SN-AAA'
        self.repair_b = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Diag B',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_a.batch_id.id,
            'serial_number': 'SN-BBB',
        })
        self.repair_b._action_repair_confirm()
        self.so_a = self._make_sale_order_linked(self.repair_a)
        self.so_b = self._make_sale_order_linked(self.repair_b)
        self.so_a.action_confirm()
        self.so_b.action_confirm()

    def test_injects_one_header_per_source_so(self):
        moves = (self.so_a + self.so_b)._create_invoices()
        # Native may produce one consolidated move for same partner
        self.assertEqual(len(moves), 1)
        move = moves
        self.repair_a.batch_id._inject_repair_section_headers(move)
        sections = move.invoice_line_ids.filtered(
            lambda l: l.display_type == 'line_section'
        )
        self.assertEqual(len(sections), 2,
                         "One section header per source sale.order")

    def test_header_label_contains_device_and_sn(self):
        moves = (self.so_a + self.so_b)._create_invoices()
        move = moves
        self.repair_a.batch_id._inject_repair_section_headers(move)
        labels = move.invoice_line_ids.filtered(
            lambda l: l.display_type == 'line_section'
        ).mapped('name')
        # repair_a.device_id_name may be empty in the test fixture; focus on SN
        self.assertTrue(any('SN-AAA' in lbl for lbl in labels),
                        "Header must include the serial number")
        self.assertTrue(any('SN-BBB' in lbl for lbl in labels),
                        "Header must include both serial numbers")
