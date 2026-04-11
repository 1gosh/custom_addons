from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestBatchIntegration(RepairAppointmentCase):

    def test_create_pickup_appointment_creates_pending(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        self.assertEqual(apt.state, 'pending')
        self.assertEqual(apt.batch_id, batch)
        self.assertFalse(apt.notification_sent_at)

    def test_create_pickup_appointment_idempotent(self):
        batch = self._make_batch()
        apt1 = batch.action_create_pickup_appointment(notify=False)
        apt2 = batch.action_create_pickup_appointment(notify=False)
        self.assertEqual(apt1, apt2)
        self.assertEqual(len(batch.appointment_ids), 1)

    def test_current_appointment_id_points_to_non_terminal(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        self.assertEqual(batch.current_appointment_id, apt)
        apt.action_cancel()
        self.assertFalse(batch.current_appointment_id)

    def test_new_appointment_after_cancel_is_allowed(self):
        batch = self._make_batch()
        apt1 = batch.action_create_pickup_appointment(notify=False)
        apt1.action_cancel()
        apt2 = batch.action_create_pickup_appointment(notify=False)
        self.assertNotEqual(apt1, apt2)
        self.assertEqual(apt2.state, 'pending')

    def test_create_pickup_appointment_notify_sets_timestamp(self):
        """notify=True sends the pickup-ready template and stamps
        notification_sent_at so the reminder CRON can find the record."""
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=True)
        self.assertEqual(apt.state, 'pending')
        self.assertTrue(apt.notification_sent_at)
