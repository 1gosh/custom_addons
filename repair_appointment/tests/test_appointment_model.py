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
