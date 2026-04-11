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

    def test_create_repair_with_explicit_batch_keeps_it(self):
        batch = self.env['repair.batch'].create({'partner_id': self.partner.id})
        repair = self.Repair.create({
            'partner_id': self.partner.id,
            'batch_id': batch.id,
        })
        self.assertEqual(repair.batch_id, batch)
        self.assertEqual(len(batch.repair_ids), 1)

    def test_action_add_device_to_batch_reuses_existing(self):
        first = self._make_repair()
        action = first.action_add_device_to_batch()
        self.assertEqual(action['context']['default_batch_id'], first.batch_id.id)
        second = self.Repair.create({
            'partner_id': self.partner.id,
            'batch_id': first.batch_id.id,
        })
        self.assertEqual(second.batch_id, first.batch_id)
        self.assertEqual(len(first.batch_id.repair_ids), 2)

    def test_batch_id_required_constraint(self):
        repair = self._make_repair()
        with self.assertRaises(ValidationError):
            repair.write({'batch_id': False})

    def test_pre_migration_wraps_batchless_repairs(self):
        """Simulate pre-upgrade state, run migrate(), assert batch was backfilled."""
        import importlib.util
        import os
        from odoo.modules.module import get_module_path

        repair = self._make_repair()
        # In the running DB the column already has NOT NULL (enforced by the
        # required=True we just added). Drop it locally inside this transaction
        # so we can simulate the pre-upgrade state. The test transaction rolls
        # back at teardown, so this is isolated.
        self.env.cr.execute(
            "ALTER TABLE repair_order ALTER COLUMN batch_id DROP NOT NULL"
        )
        self.env.cr.execute(
            "UPDATE repair_order SET batch_id = NULL WHERE id = %s",
            (repair.id,),
        )
        repair.invalidate_recordset(['batch_id'])
        self.assertFalse(repair.batch_id)

        migration_path = os.path.join(
            get_module_path('repair_custom'),
            'migrations', '17.0.1.5.0', 'pre-migration.py',
        )
        spec = importlib.util.spec_from_file_location(
            'repair_custom_pre_migration_17_0_1_5_0', migration_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, '17.0.1.5.0')

        repair.invalidate_recordset(['batch_id'])
        self.assertTrue(repair.batch_id, "pre-migration should backfill batch_id")
        self.assertEqual(repair.batch_id.partner_id, self.partner)
