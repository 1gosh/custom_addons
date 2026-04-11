from datetime import datetime, timedelta
from odoo.exceptions import UserError
from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestStateMachine(RepairAppointmentCase):

    def _make_pending(self):
        return self.Appointment.create({'batch_id': self._make_batch().id})

    def _future_slot(self, days=3):
        start = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=days)
        end = start + timedelta(hours=2, minutes=15)
        return start, end

    def test_action_schedule_moves_pending_to_scheduled(self):
        apt = self._make_pending()
        start, end = self._future_slot()
        apt.with_context(skip_slot_validation=True).action_schedule(start, end)
        self.assertEqual(apt.state, 'scheduled')
        self.assertEqual(apt.start_datetime, start)
        self.assertEqual(apt.end_datetime, end)
        self.assertEqual(apt.reschedule_count, 0)

    def test_action_schedule_in_place_reschedule_increments_count(self):
        apt = self._make_pending()
        start, end = self._future_slot(days=3)
        apt.with_context(skip_slot_validation=True).action_schedule(start, end)
        new_start, new_end = self._future_slot(days=5)
        apt.with_context(skip_slot_validation=True).action_schedule(new_start, new_end)
        self.assertEqual(apt.state, 'scheduled')
        self.assertEqual(apt.start_datetime, new_start)
        self.assertEqual(apt.reschedule_count, 1)

    def test_action_mark_done_requires_scheduled(self):
        apt = self._make_pending()
        with self.assertRaises(UserError):
            apt.action_mark_done()

    def test_action_mark_done_from_scheduled(self):
        apt = self._make_pending()
        start, end = self._future_slot()
        apt.with_context(skip_slot_validation=True).action_schedule(start, end)
        apt.action_mark_done()
        self.assertEqual(apt.state, 'done')

    def test_action_mark_no_show_from_scheduled(self):
        apt = self._make_pending()
        start, end = self._future_slot()
        apt.with_context(skip_slot_validation=True).action_schedule(start, end)
        apt.action_mark_no_show()
        self.assertEqual(apt.state, 'no_show')

    def test_action_cancel_from_pending(self):
        apt = self._make_pending()
        apt.action_cancel()
        self.assertEqual(apt.state, 'cancelled')

    def test_action_cancel_from_scheduled(self):
        apt = self._make_pending()
        start, end = self._future_slot()
        apt.with_context(skip_slot_validation=True).action_schedule(start, end)
        apt.action_cancel()
        self.assertEqual(apt.state, 'cancelled')

    def test_cannot_cancel_from_terminal_state(self):
        apt = self._make_pending()
        apt.action_cancel()
        with self.assertRaises(UserError):
            apt.action_cancel()
