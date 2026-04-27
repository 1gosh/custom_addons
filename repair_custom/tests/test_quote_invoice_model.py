# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.tests import tagged
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
        # Shared product needed for stock.lot (repairs have no device product by default)
        lot_product = self.env['product.product'].create({
            'name': 'Test Device SN',
            'type': 'product',
            'tracking': 'serial',
        })
        # Two repairs in one batch, each with its own sale.order
        self.repair_a = self._make_repair(internal_notes='Diag A')
        self.repair_a.lot_id = self.env['stock.lot'].create({
            'name': 'SN-AAA',
            'product_id': lot_product.id,
            'company_id': self.repair_a.company_id.id,
        })
        self.repair_b = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Diag B',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_a.batch_id.id,
        })
        self.repair_b.lot_id = self.env['stock.lot'].create({
            'name': 'SN-BBB',
            'product_id': lot_product.id,
            'company_id': self.repair_b.company_id.id,
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


class TestSectionHeaderInjectionWizardSOs(RepairQuoteCase):
    """Regression: SOs built by repair.pricing.wizard already carry their own
    section/note structure (header section + products + 'Détails' section +
    note). Consolidation must preserve that structure rather than duplicating
    headers or interleaving lines across SOs."""

    def setUp(self):
        super().setUp()
        lot_product = self.env['product.product'].create({
            'name': 'Test Device SN',
            'type': 'product',
            'tracking': 'serial',
        })
        self.repair_a = self._make_repair(internal_notes='Diag A')
        self.repair_a.lot_id = self.env['stock.lot'].create({
            'name': 'SN-AAA',
            'product_id': lot_product.id,
            'company_id': self.repair_a.company_id.id,
        })
        self.repair_b = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Diag B',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_a.batch_id.id,
        })
        self.repair_b.lot_id = self.env['stock.lot'].create({
            'name': 'SN-BBB',
            'product_id': lot_product.id,
            'company_id': self.repair_b.company_id.id,
        })
        self.repair_b._action_repair_confirm()
        self.so_a = self._make_wizard_so(self.repair_a, total=120.0)
        self.so_b = self._make_wizard_so(self.repair_b, total=80.0)
        self.so_a.action_confirm()
        self.so_b.action_confirm()
        self.batch = self.repair_a.batch_id

    def _make_wizard_so(self, repair, total):
        wizard = self.env['repair.pricing.wizard'].with_context(
            default_repair_id=repair.id,
        ).create({
            'repair_id': repair.id,
            'target_total_amount': total,
            'manual_product_id': self.service_product.id,
            'manual_label': "Forfait Atelier / Main d'œuvre",
            'add_work_details': True,
            'work_details': "Détails travaux %s" % repair.id,
        })
        wizard.action_confirm()
        return repair.sale_order_id

    def test_no_duplicate_repair_section_per_so(self):
        result = self.batch._invoice_approved_quotes(
            self.repair_a + self.repair_b
        )
        move = self.env['account.move'].browse(result['res_id'])
        repair_sections = move.invoice_line_ids.filtered(
            lambda l: l.display_type == 'line_section'
                      and l.name and l.name.startswith('Réparation')
        )
        self.assertEqual(
            len(repair_sections), 2,
            "Exactly one 'Réparation : ...' header per SO — no duplicates",
        )

    def test_per_so_lines_are_contiguous(self):
        result = self.batch._invoice_approved_quotes(
            self.repair_a + self.repair_b
        )
        move = self.env['account.move'].browse(result['res_id'])
        sorted_lines = move.invoice_line_ids.sorted('sequence')
        positions_a, positions_b = [], []
        for idx, line in enumerate(sorted_lines):
            sos = line.sale_line_ids.mapped('order_id')
            if self.so_a in sos:
                positions_a.append(idx)
            elif self.so_b in sos:
                positions_b.append(idx)
        self.assertTrue(positions_a and positions_b,
                        "Both SOs contributed invoice lines")
        self.assertEqual(
            positions_a, list(range(positions_a[0], positions_a[-1] + 1)),
            "SO_A's invoice lines must form a contiguous block",
        )
        self.assertEqual(
            positions_b, list(range(positions_b[0], positions_b[-1] + 1)),
            "SO_B's invoice lines must form a contiguous block",
        )

    def test_wizard_section_note_structure_preserved(self):
        result = self.batch._invoice_approved_quotes(
            self.repair_a + self.repair_b
        )
        move = self.env['account.move'].browse(result['res_id'])
        sorted_lines = move.invoice_line_ids.sorted('sequence')
        types_by_so = {}
        for line in sorted_lines:
            so = line.sale_line_ids.mapped('order_id')[:1]
            if not so:
                continue
            types_by_so.setdefault(so.id, []).append(
                line.display_type or 'product'
            )
        self.assertEqual(set(types_by_so), {self.so_a.id, self.so_b.id})
        for so_id, types in types_by_so.items():
            self.assertEqual(
                types[0], 'line_section',
                "SO %s's block starts with its 'Réparation : ...' header" % so_id,
            )
            self.assertEqual(
                types[-1], 'line_note',
                "SO %s's block ends with the work-details note" % so_id,
            )


class TestInvoiceApprovedQuotes(RepairQuoteCase):
    """Core consolidation helper used by all three button surfaces."""

    def setUp(self):
        super().setUp()
        # Shared product needed for stock.lot (repairs have no device product by default)
        lot_product = self.env['product.product'].create({
            'name': 'Test Device SN',
            'type': 'product',
            'tracking': 'serial',
        })
        self.repair_a = self._make_repair()
        self.repair_a.lot_id = self.env['stock.lot'].create({
            'name': 'SN-A',
            'product_id': lot_product.id,
            'company_id': self.repair_a.company_id.id,
        })
        self.repair_b = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Diag B',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_a.batch_id.id,
        })
        self.repair_b.lot_id = self.env['stock.lot'].create({
            'name': 'SN-B',
            'product_id': lot_product.id,
            'company_id': self.repair_b.company_id.id,
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


@tagged('post_install', '-at_install', 'repair_custom')
class TestPartialAcceptancePickup(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair_ok = self._make_repair()
        self.repair_refused = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Refused',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_ok.batch_id.id,
        })
        self.repair_refused._action_repair_confirm()

        # Approve repair_ok's quote, refuse repair_refused's quote
        so_ok = self._make_sale_order_linked(self.repair_ok)
        so_ok.action_confirm()
        self.repair_ok.state = 'done'

        so_refused = self._make_sale_order_linked(self.repair_refused)
        so_refused.action_cancel()
        self.assertEqual(self.repair_refused.quote_state, 'refused')

        self.batch = self.repair_ok.batch_id

    def test_livrer_includes_refused_quote_repairs(self):
        self.batch.action_mark_delivered()
        self.assertEqual(self.repair_ok.delivery_state, 'delivered')
        self.assertEqual(self.repair_refused.delivery_state, 'delivered',
                         "Refused-quote repair picked up un-repaired")

    def test_refused_delivery_cancels_repair_state(self):
        self.batch.action_mark_delivered()
        self.assertEqual(self.repair_refused.state, 'cancel',
                         "Silent side effect: state -> cancel for refused pickup")

    def test_refused_delivery_leaves_approved_state_alone(self):
        self.batch.action_mark_delivered()
        self.assertEqual(self.repair_ok.state, 'done',
                         "Approved+done repair's state unchanged")

    def test_batch_delivery_state_reaches_delivered(self):
        self.batch.action_mark_delivered()
        self.batch.invalidate_recordset(['delivery_state'])
        self.assertEqual(self.batch.delivery_state, 'delivered')

    def test_livrer_handles_refused_and_already_cancelled(self):
        """Regression: refused-quote repair already in state='cancel' must not
        crash action_repair_delivered."""
        self.repair_refused.state = 'cancel'  # manager pre-cancelled
        self.batch.action_mark_delivered()
        self.assertEqual(self.repair_refused.delivery_state, 'delivered')
        self.assertEqual(self.repair_refused.state, 'cancel')

    def test_livrer_multi_approved_no_singleton_crash(self):
        """Regression: action_repair_delivered is called with multi-record
        recordsets from action_mark_delivered. Direct field access on `self`
        in action_repair_delivered must not call ensure_one()."""
        # Add a second approved+done repair to the batch
        second = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Second approved',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.batch.id,
        })
        second._action_repair_confirm()
        so_second = self._make_sale_order_linked(second)
        so_second.action_confirm()
        second.state = 'done'
        # batch now has repair_ok (done/approved), second (done/approved),
        # repair_refused (refused). action_mark_delivered passes the two
        # approved repairs as a multi-record recordset to
        # action_repair_delivered — must not raise ensure_one().
        self.batch.action_mark_delivered()
        self.assertEqual(self.repair_ok.delivery_state, 'delivered')
        self.assertEqual(second.delivery_state, 'delivered')
        self.assertEqual(self.repair_refused.delivery_state, 'delivered')


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


class TestPricingWizardQuoteOnly(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()

    def test_wizard_has_no_generation_type_field(self):
        Wizard = self.env['repair.pricing.wizard']
        self.assertNotIn('generation_type', Wizard._fields,
                         "generation_type removed in Theme A")

    def test_wizard_has_no_batch_fields(self):
        Wizard = self.env['repair.pricing.wizard']
        for f in ('batch_id', 'remaining_repair_ids',
                  'accumulated_lines_json', 'step_info'):
            self.assertNotIn(f, Wizard._fields,
                             f"{f} removed in Theme A")

    def test_wizard_creates_quote_only(self):
        wizard = self.env['repair.pricing.wizard'].with_context(
            default_repair_id=self.repair.id
        ).create({
            'repair_id': self.repair.id,
            'target_total_amount': 100.0,
            'manual_product_id': self.service_product.id,
            'manual_label': 'Forfait test',
        })
        result = wizard.action_confirm()
        self.assertEqual(result['res_model'], 'sale.order',
                         "Wizard produces a sale.order, not an account.move")
        so = self.env['sale.order'].browse(result['res_id'])
        self.assertEqual(so, self.repair.sale_order_id)
        self.assertEqual(so.sale_order_template_id,
                         self.env.ref('repair_custom.sale_order_template_repair_quote'))

    def test_wizard_rejects_duplicate_quote(self):
        from odoo.exceptions import UserError
        self._make_sale_order_linked(self.repair)
        wizard = self.env['repair.pricing.wizard'].create({
            'repair_id': self.repair.id,
            'target_total_amount': 50.0,
            'manual_product_id': self.service_product.id,
            'manual_label': 'Double',
        })
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_wizard_ignores_batch_context(self):
        """Launching with active_model='repair.batch' no longer pre-fills a
        batch walkthrough — Theme A removes that entry path."""
        wizard_env = self.env['repair.pricing.wizard'].with_context(
            active_model='repair.batch',
            active_id=self.repair.batch_id.id,
            default_repair_id=self.repair.id,
        )
        # default_get should not populate anything batch-shaped (fields don't exist)
        # and should still resolve the repair_id from default_repair_id
        defaults = wizard_env.default_get(['repair_id', 'device_name'])
        self.assertEqual(defaults.get('repair_id'), self.repair.id)
