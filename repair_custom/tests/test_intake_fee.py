# -*- coding: utf-8 -*-
from odoo.exceptions import UserError

from .common import RepairQuoteCase


class TestIntakeFee(RepairQuoteCase):
    """Prise en charge / Diagnostic — collected at drop-off, deducted from the
    final quote, non-refundable on refusal."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fee_product = cls.env.ref('repair_custom.product_intake_fee')
        cls.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.intake_fee_amount_ht', '50.0'
        )

    def _make_batch_repairs(self, count):
        """Create `count` repairs sharing a single dossier, mirroring how the
        counter groups several devices from the same customer."""
        Batch = self.env['repair.batch']
        batch = Batch.create({'partner_id': self.partner.id})
        repairs = self.env['repair.order']
        for _i in range(count):
            repair = self.Repair.create({
                'partner_id': self.partner.id,
                'batch_id': batch.id,
                'technician_employee_id': self.tech_with_user.id,
            })
            repairs |= repair
        for repair in repairs:
            repair._action_repair_confirm()
        return batch, repairs

    def _collect_fee(self, batch):
        wizard = self.env['repair.intake.fee.wizard'].with_context(
            default_batch_id=batch.id
        ).create({})
        wizard.action_confirm()
        return wizard

    # --- Exemption ---

    def test_sar_sav_auto_exempt(self):
        repair = self._make_repair()
        repair.repair_warranty = 'sar'
        self.assertEqual(repair.intake_fee_state, 'not_due')
        repair.repair_warranty = 'sav'
        self.assertEqual(repair.intake_fee_state, 'not_due')
        repair.repair_warranty = 'aucune'
        self.assertEqual(repair.intake_fee_state, 'to_collect')

    def test_draft_and_cancel_are_not_due(self):
        repair = self.Repair.create({
            'partner_id': self.partner.id,
            'technician_employee_id': self.tech_with_user.id,
        })
        self.assertEqual(repair.intake_fee_state, 'not_due')

    # --- Consolidated invoicing per dossier ---

    def test_batch_consolidated_invoice_three_devices(self):
        batch, repairs = self._make_batch_repairs(3)
        self._collect_fee(batch)

        moves = repairs.mapped('intake_fee_invoice_id')
        self.assertEqual(len(moves), 1, "All three devices share one invoice")
        move = moves
        self.assertEqual(move.batch_id, batch)
        self.assertEqual(len(move.invoice_line_ids.filtered(
            lambda l: l.product_id == self.fee_product
        )), 3)
        self.assertEqual(move.state, 'posted')
        for repair in repairs:
            self.assertEqual(repair.intake_fee_state, 'invoiced')
            self.assertEqual(repair.intake_fee_amount_ht, 50.0)

    def test_invoice_count_includes_intake_fee_invoice(self):
        batch, repairs = self._make_batch_repairs(1)
        repair = repairs
        self.assertEqual(repair.invoice_count, 0)
        self._collect_fee(batch)
        self.assertEqual(repair.invoice_count, 1)

    def test_exempt_line_excluded_from_invoice(self):
        batch, repairs = self._make_batch_repairs(2)
        r1, r2 = repairs
        r1.repair_warranty = 'sar'

        wizard = self.env['repair.intake.fee.wizard'].with_context(
            default_batch_id=batch.id
        ).create({})
        # Only r2 should default to "to collect" since r1 is auto-exempt.
        self.assertEqual(wizard.line_ids.mapped('repair_id'), r2)
        wizard.action_confirm()

        self.assertEqual(r2.intake_fee_state, 'invoiced')
        self.assertFalse(r1.intake_fee_invoice_id)

    # --- Deduction on the repair quote ---

    def test_deduction_present_on_quote_and_final_invoice(self):
        batch, repairs = self._make_batch_repairs(1)
        repair = repairs
        self._collect_fee(batch)

        wizard = self.env['repair.pricing.wizard'].with_context(
            default_repair_id=repair.id
        ).create({
            'repair_id': repair.id,
            'target_total_amount': 250.0,
            'manual_product_id': self.service_product.id,
            'manual_label': 'Forfait test',
            'add_work_details': False,
        })
        wizard.action_confirm()

        self.assertTrue(repair.intake_fee_deduction_line_id)
        self.assertEqual(repair.intake_fee_state, 'deducted')
        deduction_line = repair.intake_fee_deduction_line_id
        self.assertAlmostEqual(deduction_line.price_unit, -50.0)

        so = repair.sale_order_id
        so.action_confirm()
        moves = so._create_invoices()
        move = moves
        fee_lines = move.invoice_line_ids.filtered(
            lambda l: l.product_id == self.fee_product
        )
        self.assertEqual(len(fee_lines), 1)
        self.assertAlmostEqual(fee_lines.price_unit, -50.0)
        # 250 (labour) - 50 (fee) = 200 net HT before tax.
        self.assertAlmostEqual(sum(so.order_line.mapped('price_subtotal')), 200.0)

    def test_deduction_capped_never_negative(self):
        batch, repairs = self._make_batch_repairs(1)
        repair = repairs
        self._collect_fee(batch)

        wizard = self.env['repair.pricing.wizard'].with_context(
            default_repair_id=repair.id
        ).create({
            'repair_id': repair.id,
            'target_total_amount': 30.0,  # below the 50 HT fee
            'manual_product_id': self.service_product.id,
            'manual_label': 'Petit forfait',
            'add_work_details': False,
        })
        wizard.action_confirm()

        so = repair.sale_order_id
        self.assertGreaterEqual(sum(so.order_line.mapped('price_subtotal')), 0.0)
        deduction_line = repair.intake_fee_deduction_line_id
        self.assertAlmostEqual(deduction_line.price_unit, -30.0)

    def test_apply_deduction_catch_up(self):
        """Fee collected after the quote already exists."""
        batch, repairs = self._make_batch_repairs(1)
        repair = repairs

        wizard = self.env['repair.pricing.wizard'].with_context(
            default_repair_id=repair.id
        ).create({
            'repair_id': repair.id,
            'target_total_amount': 250.0,
            'manual_product_id': self.service_product.id,
            'manual_label': 'Forfait test',
            'add_work_details': False,
        })
        wizard.action_confirm()
        self.assertFalse(repair.intake_fee_deduction_line_id)

        self._collect_fee(batch)
        self.assertEqual(repair.intake_fee_state, 'invoiced')

        repair.action_apply_intake_fee_deduction()
        self.assertTrue(repair.intake_fee_deduction_line_id)
        self.assertEqual(repair.intake_fee_state, 'deducted')

    def test_apply_deduction_raises_without_payment(self):
        batch, repairs = self._make_batch_repairs(1)
        repair = repairs
        wizard = self.env['repair.pricing.wizard'].with_context(
            default_repair_id=repair.id
        ).create({
            'repair_id': repair.id,
            'target_total_amount': 250.0,
            'manual_product_id': self.service_product.id,
        })
        wizard.action_confirm()
        with self.assertRaises(UserError):
            repair.action_apply_intake_fee_deduction()

    # --- Refusal keeps the fee ---

    def test_refusal_keeps_intake_fee_invoice_intact(self):
        batch, repairs = self._make_batch_repairs(1)
        repair = repairs
        self._collect_fee(batch)
        fee_invoice = repair.intake_fee_invoice_id

        self._make_sale_order_linked(repair)
        repair.sale_order_id.action_cancel()
        self.assertEqual(repair.quote_state, 'refused')

        batch.action_mark_delivered()
        self.assertEqual(repair.state, 'cancel')
        self.assertEqual(repair.delivery_state, 'delivered')

        # The intake fee invoice is untouched: still posted, no credit note.
        self.assertEqual(fee_invoice.state, 'posted')
        self.assertEqual(repair.intake_fee_invoice_id, fee_invoice)
        self.assertFalse(fee_invoice.reversal_move_id)

    def test_partial_batch_mixed_accept_refuse(self):
        batch, repairs = self._make_batch_repairs(2)
        r1, r2 = repairs
        self._collect_fee(batch)

        self._make_sale_order_linked(r1)
        r1.sale_order_id.action_cancel()
        self.assertEqual(r1.quote_state, 'refused')

        r2.state = 'done'
        r2.quote_state = 'approved'

        batch.action_mark_delivered()
        self.assertEqual(r1.state, 'cancel')
        self.assertEqual(r1.delivery_state, 'delivered')
        self.assertEqual(r2.delivery_state, 'delivered')

        # Both fee invoices remain intact regardless of outcome.
        self.assertTrue(r1.intake_fee_invoice_id.state == 'posted')
        self.assertTrue(r2.intake_fee_invoice_id.state == 'posted')
