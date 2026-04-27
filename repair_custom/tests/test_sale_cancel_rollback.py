# -*- coding: utf-8 -*-
"""Cancel rollback tests for issue B.

Equipment-sale cancel must clear SAV / sale stamps from the lot.
Rental cancel must move the unit back from Rented to Stock and reset
rental_state. Already-returned rentals must not be re-transferred.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class TestSaleCancelRollback(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.SaleOrder = cls.env['sale.order']
        cls.Lot = cls.env['stock.lot']
        cls.Quant = cls.env['stock.quant']

        cls.partner = cls.env['res.partner'].create({
            'name': 'Cancel Rollback Customer',
        })

        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1
        )
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.rented_location = cls.env.ref('repair_custom.stock_location_rented')

        cls.hifi_category = cls.env.ref('repair_devices.product_category_hifi')
        cls.brand = cls.env['repair.device.brand'].create({'name': 'TestBrand'})
        cls.product_tmpl = cls.env['product.template'].create({
            'name': 'Test Amplifier',
            'categ_id': cls.hifi_category.id,
            'brand_id': cls.brand.id,
            # _check_hifi_device_config validates these but the create override
            # already stamps them — set defensively in case install order shifts.
            'detailed_type': 'product',
            'tracking': 'serial',
            'sale_ok': True,
            'list_price': 500.0,
        })
        cls.product = cls.product_tmpl.product_variant_id

        cls.equipment_sale_tmpl = cls.env.ref(
            'repair_custom.sale_order_template_equipment_sale'
        )
        cls.rental_tmpl = cls.env.ref(
            'repair_custom.sale_order_template_rental'
        )

    def _make_lot_in_stock(self, name):
        """Create a HiFi lot with one positive quant at WH/Stock."""
        lot = self.Lot.create({
            'name': name,
            'product_id': self.product.id,
            'company_id': self.env.company.id,
        })
        self.Quant._update_available_quantity(
            self.product, self.stock_location, 1.0, lot_id=lot,
        )
        return lot

    def _make_so(self, template, lot, **extra):
        vals = {
            'partner_id': self.partner.id,
            'sale_order_template_id': template.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': self.product.display_name,
                'product_uom_qty': 1.0,
                'price_unit': 500.0,
                'lot_id': lot.id,
            })],
        }
        vals.update(extra)
        return self.SaleOrder.create(vals)

    # ---------------------------------------------------------------
    # Equipment sale
    # ---------------------------------------------------------------

    def test_equipment_sale_confirm_stamps_lot(self):
        """Sanity check: confirm stamps SAV fields on the lot."""
        lot = self._make_lot_in_stock('SAV-001')
        so = self._make_so(self.equipment_sale_tmpl, lot)
        self.assertEqual(so.computed_order_type, 'equipment_sale')

        so.action_confirm()

        self.assertEqual(lot.sale_order_id, so)
        self.assertTrue(lot.sav_expiry)
        self.assertTrue(lot.sale_date)
        self.assertEqual(lot.hifi_partner_id, self.partner)

    def test_equipment_sale_cancel_clears_lot_stamps(self):
        """Cancel after confirm wipes SAV/sale fields on the lot."""
        lot = self._make_lot_in_stock('SAV-002')
        so = self._make_so(self.equipment_sale_tmpl, lot)
        so.action_confirm()

        # Sanity precondition
        self.assertTrue(lot.sav_expiry)
        self.assertEqual(lot.sale_order_id, so)

        so._action_cancel()

        self.assertFalse(lot.sav_expiry)
        self.assertFalse(lot.sale_date)
        self.assertFalse(lot.sale_order_id)
        self.assertFalse(lot.hifi_partner_id)
        self.assertEqual(so.state, 'cancel')

    def test_equipment_sale_cancel_does_not_touch_other_lots(self):
        """Cancelling SO A must not clear stamps left by SO B on a different lot."""
        lot_a = self._make_lot_in_stock('SAV-003-A')
        lot_b = self._make_lot_in_stock('SAV-003-B')
        so_a = self._make_so(self.equipment_sale_tmpl, lot_a)
        so_b = self._make_so(self.equipment_sale_tmpl, lot_b)
        so_a.action_confirm()
        so_b.action_confirm()

        so_a._action_cancel()

        self.assertFalse(lot_a.sale_order_id)
        self.assertEqual(lot_b.sale_order_id, so_b, "SO B's lot must be untouched")
        self.assertTrue(lot_b.sav_expiry)

    # ---------------------------------------------------------------
    # Rental
    # ---------------------------------------------------------------

    def _confirm_rental(self, lot):
        today = fields.Datetime.now()
        so = self._make_so(
            self.rental_tmpl, lot,
            rental_start_date=today,
            rental_end_date=today + timedelta(days=7),
        )
        so.action_confirm()
        return so

    def test_rental_confirm_moves_unit_to_rented(self):
        """Sanity check: confirm transfers the unit to Rented and flips state."""
        lot = self._make_lot_in_stock('RENT-001')
        so = self._confirm_rental(lot)
        self.assertEqual(so.computed_order_type, 'rental')
        self.assertEqual(so.rental_state, 'active')
        self.assertEqual(lot.location_id, self.rented_location)

    def test_rental_cancel_active_returns_unit_to_stock(self):
        """Cancelling an active rental moves the unit back to Stock and
        resets rental_state to 'draft'."""
        lot = self._make_lot_in_stock('RENT-002')
        so = self._confirm_rental(lot)
        self.assertEqual(lot.location_id, self.rented_location)

        so._action_cancel()

        self.assertEqual(lot.location_id, self.stock_location)
        self.assertEqual(so.rental_state, 'draft')
        self.assertEqual(so.state, 'cancel')

    def test_rental_cancel_after_return_does_not_double_transfer(self):
        """Returning then cancelling must not produce a second transfer
        (idempotency)."""
        lot = self._make_lot_in_stock('RENT-003')
        so = self._confirm_rental(lot)
        so.action_return_rental()
        self.assertEqual(so.rental_state, 'returned')
        self.assertEqual(lot.location_id, self.stock_location)
        pickings_before = so.picking_ids | self.env['stock.picking'].search(
            [('origin', '=', so.name)]
        )
        n_before = len(pickings_before)

        so._action_cancel()

        pickings_after = self.env['stock.picking'].search(
            [('origin', '=', so.name)]
        )
        self.assertEqual(
            len(pickings_after), n_before,
            "Cancelling an already-returned rental must not create a new picking",
        )
        self.assertEqual(lot.location_id, self.stock_location)
        self.assertEqual(so.state, 'cancel')

    def test_action_cancel_idempotent_on_already_cancelled_so(self):
        """Calling _action_cancel a second time must not blow up or re-touch
        already-cleared lot fields."""
        lot = self._make_lot_in_stock('SAV-004')
        so = self._make_so(self.equipment_sale_tmpl, lot)
        so.action_confirm()
        so._action_cancel()
        self.assertFalse(lot.sav_expiry)

        # Second call: noop on the rollback path; sale.order's super may also
        # accept the second call gracefully.
        so._action_cancel()
        self.assertFalse(lot.sav_expiry)
        self.assertEqual(so.state, 'cancel')
