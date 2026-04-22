# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('-at_install', 'post_install', 'repair_custom')
class RepairBatchUxCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Test Partner UX'})
        cls.product_tmpl = cls.env['product.template'].create({
            'name': 'UX Test Device',
            'type': 'product',
            'tracking': 'serial',
        })
        cls.Repair = cls.env['repair.order']
        cls.Batch = cls.env['repair.batch']

    def _new_draft_repair(self, **overrides):
        vals = {
            'partner_id': self.partner.id,
            'product_tmpl_id': self.product_tmpl.id,
        }
        vals.update(overrides)
        return self.Repair.create(vals)


@tagged('-at_install', 'post_install', 'repair_custom')
class TestDeferredBatchCreation(RepairBatchUxCommon):
    def test_create_repair_without_batch(self):
        repair = self._new_draft_repair()
        self.assertFalse(
            repair.batch_id,
            "Draft repair must not have a batch populated at create()",
        )
        self.assertEqual(repair.state, 'draft')

    def test_confirm_creates_batch_when_missing(self):
        repair = self._new_draft_repair()
        self.assertFalse(repair.batch_id)
        repair._action_repair_confirm()
        self.assertTrue(repair.batch_id, "action_confirm must populate batch_id")
        self.assertEqual(repair.batch_id.partner_id, self.partner)
        self.assertEqual(repair.state, 'confirmed')

    def test_confirm_keeps_existing_batch(self):
        existing = self.Batch.create({'partner_id': self.partner.id})
        repair = self._new_draft_repair(batch_id=existing.id)
        repair._action_repair_confirm()
        self.assertEqual(repair.batch_id, existing)

    def test_confirm_requires_partner(self):
        # repair_order.partner_id has a NOT NULL DB constraint, so we use an
        # in-memory (NewId) record to exercise the UserError guard inside
        # action_confirm without hitting the DB-level check.
        repair = self.Repair.new({'product_tmpl_id': self.product_tmpl.id})
        with self.assertRaises(UserError):
            repair._action_repair_confirm()

    def test_constraint_fires_when_clearing_batch_post_draft(self):
        repair = self._new_draft_repair()
        repair._action_repair_confirm()
        self.assertTrue(repair.batch_id)
        with self.assertRaises(ValidationError):
            repair.batch_id = False

    def test_action_add_device_to_batch_unchanged(self):
        # Confirm the first repair so the batch exists
        r1 = self._new_draft_repair()
        r1._action_repair_confirm()
        batch = r1.batch_id
        self.assertTrue(batch)

        # Simulate the existing add-device flow: create a sibling draft that
        # points at the same batch explicitly (mirrors the wizard behavior).
        r2 = self.Repair.create({
            'partner_id': self.partner.id,
            'product_tmpl_id': self.product_tmpl.id,
            'batch_id': batch.id,
        })
        self.assertEqual(r2.batch_id, batch)


@tagged('-at_install', 'post_install', 'repair_custom')
class TestArchiveCascade(RepairBatchUxCommon):
    def _confirmed(self, **overrides):
        r = self._new_draft_repair(**overrides)
        r._action_repair_confirm()
        return r

    def test_unlink_last_repair_archives_batch(self):
        repair = self._confirmed()
        batch = repair.batch_id
        repair.unlink()
        self.assertFalse(batch.active, "Batch must be archived after last repair deleted")

    def test_unlink_with_siblings_keeps_batch_active(self):
        r1 = self._confirmed()
        batch = r1.batch_id
        r2 = self.Repair.create({
            'partner_id': self.partner.id,
            'product_tmpl_id': self.product_tmpl.id,
            'batch_id': batch.id,
        })
        r2._action_repair_confirm()
        r1.unlink()
        self.assertTrue(batch.active)

    def test_archive_last_active_repair_archives_batch(self):
        repair = self._confirmed()
        batch = repair.batch_id
        repair.active = False
        self.assertFalse(batch.active)

    def test_unarchive_repair_unarchives_batch(self):
        repair = self._confirmed()
        batch = repair.batch_id
        repair.active = False
        self.assertFalse(batch.active)
        repair.active = True
        self.assertTrue(batch.active)

    def test_archive_with_active_siblings_keeps_batch_active(self):
        r1 = self._confirmed()
        batch = r1.batch_id
        r2 = self.Repair.create({
            'partner_id': self.partner.id,
            'product_tmpl_id': self.product_tmpl.id,
            'batch_id': batch.id,
        })
        r2._action_repair_confirm()
        r1.active = False
        self.assertTrue(batch.active, "Batch stays active while any sibling is active")


@tagged('-at_install', 'post_install', 'repair_custom')
class TestBatchDeliveryState(RepairBatchUxCommon):
    def _confirmed(self, **overrides):
        r = self._new_draft_repair(**overrides)
        r._action_repair_confirm()
        return r

    def _make_batch_with_repairs(self, n=2):
        r1 = self._confirmed()
        batch = r1.batch_id
        repairs = r1
        for _ in range(n - 1):
            r = self.Repair.create({
                'partner_id': self.partner.id,
                'product_tmpl_id': self.product_tmpl.id,
                'batch_id': batch.id,
            })
            r._action_repair_confirm()
            repairs |= r
        return batch, repairs

    def test_delivery_state_none_default(self):
        batch, _ = self._make_batch_with_repairs(2)
        self.assertEqual(batch.delivery_state, 'none')

    def test_delivery_state_all_delivered(self):
        batch, repairs = self._make_batch_with_repairs(2)
        repairs.write({'delivery_state': 'delivered'})
        self.assertEqual(batch.delivery_state, 'delivered')

    def test_delivery_state_partial(self):
        batch, repairs = self._make_batch_with_repairs(2)
        repairs[0].delivery_state = 'delivered'
        self.assertEqual(batch.delivery_state, 'partial')

    def test_delivery_state_all_abandoned(self):
        batch, repairs = self._make_batch_with_repairs(2)
        repairs.write({'delivery_state': 'abandoned'})
        self.assertEqual(batch.delivery_state, 'abandoned')

    def test_delivery_state_ignores_abandoned_for_delivered_check(self):
        batch, repairs = self._make_batch_with_repairs(2)
        repairs[0].delivery_state = 'abandoned'
        repairs[1].delivery_state = 'delivered'
        self.assertEqual(batch.delivery_state, 'delivered')


@tagged('-at_install', 'post_install', 'repair_custom')
class TestSiblingBanner(RepairBatchUxCommon):
    def _confirmed(self, batch=None):
        overrides = {'batch_id': batch.id} if batch else {}
        r = self._new_draft_repair(**overrides)
        r._action_repair_confirm()
        return r

    def test_has_siblings_false_for_singleton(self):
        repair = self._confirmed()
        self.assertFalse(repair.has_siblings)
        self.assertFalse(repair.sibling_repair_ids)

    def test_has_siblings_true_when_batch_has_peers(self):
        r1 = self._confirmed()
        r2 = self._confirmed(batch=r1.batch_id)
        self.assertTrue(r1.has_siblings)
        self.assertTrue(r2.has_siblings)

    def test_sibling_list_excludes_self(self):
        r1 = self._confirmed()
        r2 = self._confirmed(batch=r1.batch_id)
        self.assertNotIn(r1, r1.sibling_repair_ids)
        self.assertIn(r2, r1.sibling_repair_ids)
        self.assertNotIn(r2, r2.sibling_repair_ids)
        self.assertIn(r1, r2.sibling_repair_ids)

    def test_sibling_fields_empty_when_no_batch(self):
        repair = self._new_draft_repair()
        self.assertFalse(repair.batch_id)
        self.assertFalse(repair.has_siblings)
        self.assertFalse(repair.sibling_repair_ids)


@tagged('-at_install', 'post_install', 'repair_custom')
class TestNavigationBridge(RepairBatchUxCommon):
    def _confirmed(self, batch=None):
        overrides = {'batch_id': batch.id} if batch else {}
        r = self._new_draft_repair(**overrides)
        r._action_repair_confirm()
        return r

    def test_batch_sibling_count_matches_repair_count(self):
        r1 = self._confirmed()
        self.assertEqual(r1.batch_sibling_count, 1)
        r2 = self._confirmed(batch=r1.batch_id)
        r1.invalidate_recordset(['batch_sibling_count'])
        self.assertEqual(r1.batch_sibling_count, 2)
        self.assertEqual(r2.batch_sibling_count, 2)

    def test_batch_sibling_count_zero_for_batchless(self):
        draft = self._new_draft_repair()
        self.assertFalse(draft.batch_id)
        self.assertEqual(draft.batch_sibling_count, 0)

    def test_action_open_batch_returns_form_action(self):
        r = self._confirmed()
        action = r.action_open_batch()
        self.assertEqual(action['res_model'], 'repair.batch')
        self.assertEqual(action['res_id'], r.batch_id.id)
        self.assertEqual(action['view_mode'], 'form')

    def test_action_open_batch_raises_when_no_batch(self):
        draft = self._new_draft_repair()
        with self.assertRaises(UserError):
            draft.action_open_batch()
