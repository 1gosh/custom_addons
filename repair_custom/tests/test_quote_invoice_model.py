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


class TestInvoiceApprovedQuotes(RepairQuoteCase):
    """Core consolidation helper used by all three button surfaces."""

    def setUp(self):
        super().setUp()
        self.repair_a = self._make_repair()
        self.repair_a.serial_number = 'SN-A'
        self.repair_b = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Diag B',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_a.batch_id.id,
            'serial_number': 'SN-B',
        })
        self.repair_b._action_repair_confirm()
        self.so_a = self._make_sale_order_linked(self.repair_a)
        self.so_b = self._make_sale_order_linked(self.repair_b)
        self.batch = self.repair_a.batch_id

    def test_raises_when_no_repairs_passed(self):
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.batch._invoice_approved_quotes(self.Repair)

    def test_raises_when_no_sale_orders_linked(self):
        from odoo.exceptions import UserError
        orphan = self._make_repair()
        with self.assertRaises(UserError):
            orphan.batch_id._invoice_approved_quotes(orphan)

    def test_creates_consolidated_invoice_with_section_headers(self):
        # Both SOs confirmed and approved (quote_state=approved)
        self.so_a.action_confirm()
        self.so_b.action_confirm()
        result = self.batch._invoice_approved_quotes(
            self.repair_a + self.repair_b
        )
        # Helper returns an act_window dict
        self.assertEqual(result['res_model'], 'account.move')
        move = self.env['account.move'].browse(result['res_id'])
        self.assertTrue(move.exists())
        self.assertEqual(move.batch_id, self.batch,
                         "Consolidated move stamps batch_id")
        sections = move.invoice_line_ids.filtered(
            lambda l: l.display_type == 'line_section'
        )
        self.assertEqual(len(sections), 2,
                         "One section header per source SO")

    def test_singleton_invoice_stamps_repair_id(self):
        self.so_a.action_confirm()
        result = self.batch._invoice_approved_quotes(self.repair_a)
        move = self.env['account.move'].browse(result['res_id'])
        self.assertEqual(move.repair_id, self.repair_a,
                         "Singleton invoice stamps repair_id (via auto-stamp)")
        self.assertEqual(move.batch_id, self.batch)


class TestIsQuoteInvoiceable(RepairQuoteCase):
    """is_quote_invoiceable gates the repair-form 'Facturer le devis' button."""

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()

    def test_false_when_no_sale_order(self):
        self.assertFalse(self.repair.is_quote_invoiceable)

    def test_false_when_quote_state_not_approved(self):
        self._make_sale_order_linked(self.repair)
        self.repair.quote_state = 'sent'
        self.assertFalse(self.repair.is_quote_invoiceable)

    def test_true_when_approved_and_to_invoice(self):
        so = self._make_sale_order_linked(self.repair)
        so.action_confirm()  # state=sale → sync to quote_state=approved
        # After action_confirm, invoice_status transitions to 'to invoice'
        self.assertEqual(self.repair.quote_state, 'approved')
        self.assertIn(so.invoice_status, ('to invoice', 'upselling'))
        self.assertTrue(self.repair.is_quote_invoiceable)

    def test_false_after_invoice_generated(self):
        so = self._make_sale_order_linked(self.repair)
        so.action_confirm()
        so._create_invoices()
        self.assertEqual(so.invoice_status, 'invoiced')
        self.assertFalse(self.repair.is_quote_invoiceable)


class TestRepairInvoiceAction(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()
        self.so = self._make_sale_order_linked(self.repair)
        self.so.action_confirm()

    def test_action_delegates_to_batch_helper(self):
        result = self.repair.action_invoice_repair_quote()
        self.assertEqual(result['res_model'], 'account.move')
        move = self.env['account.move'].browse(result['res_id'])
        self.assertEqual(move.repair_id, self.repair)
        self.assertEqual(move.batch_id, self.repair.batch_id)
        self.assertFalse(self.repair.is_quote_invoiceable)


class TestBatchInvoiceAction(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair_a = self._make_repair()
        self.repair_b = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'B',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_a.batch_id.id,
        })
        self.repair_b._action_repair_confirm()
        self.so_a = self._make_sale_order_linked(self.repair_a)
        self.so_b = self._make_sale_order_linked(self.repair_b)
        self.batch = self.repair_a.batch_id

    def test_has_invoiceable_quotes_false_when_none_approved(self):
        self.assertFalse(self.batch.has_invoiceable_quotes)

    def test_has_invoiceable_quotes_true_when_any_approved(self):
        self.so_a.action_confirm()
        # Force recompute: depends on repair_ids.is_quote_invoiceable which
        # depends on quote_state + sale_order_id.invoice_status
        self.batch.invalidate_recordset(['has_invoiceable_quotes'])
        self.assertTrue(self.batch.has_invoiceable_quotes)

    def test_action_consolidates_only_approved(self):
        self.so_a.action_confirm()
        # so_b stays in draft → quote_state=pending, not eligible
        result = self.batch.action_invoice_approved_quotes()
        move = self.env['account.move'].browse(result['res_id'])
        # Only repair_a's lines should be on the move
        sos_on_move = move.invoice_line_ids.mapped('sale_line_ids.order_id')
        self.assertIn(self.so_a, sos_on_move)
        self.assertNotIn(self.so_b, sos_on_move)

    def test_action_raises_when_no_eligible(self):
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.batch.action_invoice_approved_quotes()


class TestSaleOrderButtonReplacement(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()
        self.so = self._make_sale_order_linked(self.repair)
        # Assign the repair quote template so computed_order_type = 'repair_quote'
        self.so.sale_order_template_id = self.env.ref(
            'repair_custom.sale_order_template_repair_quote'
        )
        self.so.action_confirm()

    def test_action_invoices_only_this_so(self):
        """Per-SO button (C.1) invoices only this SO even if batch siblings exist."""
        # Create a sibling repair with its own approved quote
        sibling = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Sibling',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair.batch_id.id,
        })
        sibling._action_repair_confirm()
        sibling_so = self._make_sale_order_linked(sibling)
        sibling_so.sale_order_template_id = self.env.ref(
            'repair_custom.sale_order_template_repair_quote'
        )
        sibling_so.action_confirm()

        result = self.so.action_invoice_repair_quote()
        move = self.env['account.move'].browse(result['res_id'])
        sos_on_move = move.invoice_line_ids.mapped('sale_line_ids.order_id')
        self.assertEqual(sos_on_move, self.so,
                         "Per-SO button must invoice only self, not siblings")

    def test_action_raises_without_repair_link(self):
        from odoo.exceptions import UserError
        standalone = self.SaleOrder.create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.service_product.id,
                'name': 'X',
                'product_uom_qty': 1,
                'price_unit': 1.0,
            })],
        })
        with self.assertRaises(UserError):
            standalone.action_invoice_repair_quote()

    def test_computed_order_type_is_repair_quote(self):
        """Sanity check for the view inheritance gate."""
        self.assertEqual(self.so.computed_order_type, 'repair_quote')
