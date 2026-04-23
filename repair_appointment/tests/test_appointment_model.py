from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestAppointmentModel(RepairAppointmentCase):

    def test_create_appointment_generates_name_and_token(self):
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        self.assertTrue(apt.name.startswith('RDV/'), f"got: {apt.name}")
        self.assertTrue(apt.token)
        self.assertEqual(len(apt.token), 36)  # UUID4 canonical length

    def test_partner_and_location_computed_from_batch(self):
        batch = self._make_batch(location=self.location_atelier)
        apt = self.Appointment.create({'batch_id': batch.id})
        self.assertEqual(apt.partner_id, self.partner)
        self.assertEqual(apt.location_id, self.location_atelier)

    def test_default_state_is_pending(self):
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        self.assertEqual(apt.state, 'pending')

    def test_token_is_unique(self):
        batch1 = self._make_batch()
        batch2 = self._make_batch()
        apt1 = self.Appointment.create({'batch_id': batch1.id})
        apt2 = self.Appointment.create({'batch_id': batch2.id})
        self.assertNotEqual(apt1.token, apt2.token)

    def test_repair_ids_related_from_batch(self):
        batch = self._make_batch(repair_count=3)
        apt = self.Appointment.create({'batch_id': batch.id})
        self.assertEqual(len(apt.repair_ids), 3)

    def test_appointment_has_pickup_date_not_datetime(self):
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        self.assertIn('pickup_date', apt._fields)
        self.assertNotIn('start_datetime', apt._fields)
        self.assertNotIn('end_datetime', apt._fields)
        self.assertFalse(apt.pickup_date)
        self.assertEqual(apt.state, 'pending')

    def test_scheduled_requires_pickup_date(self):
        from odoo.exceptions import ValidationError
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        with self.assertRaises(ValidationError):
            apt.write({'state': 'scheduled'})

    def test_primary_device_exposes_first_repair_device(self):
        batch = self._make_batch(repair_count=2)
        repair = batch.repair_ids[0]
        # Give the first repair a product_tmpl and category so the computes fire
        template = self.env['product.template'].search([], limit=1)
        category = self.env['product.category'].search([], limit=1)
        repair.write({
            'product_tmpl_id': template.id if template else False,
            'category_id': category.id if category else False,
        })
        apt = self.Appointment.create({'batch_id': batch.id})
        if template:
            self.assertEqual(apt.primary_device_id, template)
        if category:
            self.assertEqual(apt.primary_device_category_id, category)
