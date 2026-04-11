from datetime import datetime, timedelta
from odoo.exceptions import UserError, ValidationError
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

    def test_action_confirm_manual_requires_dates(self):
        apt = self._make_pending()
        with self.assertRaises(UserError):
            apt.action_confirm_manual()

    def test_action_confirm_manual_requires_end_date(self):
        apt = self._make_pending()
        start, _end = self._future_slot()
        apt.start_datetime = start
        with self.assertRaises(UserError):
            apt.action_confirm_manual()

    def test_action_confirm_manual_transitions_pending_to_scheduled(self):
        apt = self._make_pending()
        start, end = self._future_slot()
        apt.write({'start_datetime': start, 'end_datetime': end})
        apt.action_confirm_manual()
        self.assertEqual(apt.state, 'scheduled')
        self.assertEqual(apt.start_datetime, start)
        self.assertEqual(apt.end_datetime, end)
        self.assertEqual(apt.reschedule_count, 0)

    def test_action_confirm_manual_rejects_non_pending(self):
        apt = self._make_pending()
        start, end = self._future_slot()
        apt.with_context(skip_slot_validation=True).action_schedule(start, end)
        with self.assertRaises(UserError):
            apt.action_confirm_manual()

    def test_constraint_blocks_scheduled_without_dates(self):
        apt = self._make_pending()
        with self.assertRaises(ValidationError):
            apt.state = 'scheduled'

    def test_device_summary_empty_when_no_repairs(self):
        apt = self.Appointment.create({
            'batch_id': self.Batch.create({
                'partner_id': self.partner.id,
            }).id,
        })
        self.assertEqual(apt.device_count, 0)
        self.assertTrue(apt.device_summary)  # at least "Aucun appareil"

    def test_device_summary_lists_repairs(self):
        batch = self._make_batch(repair_count=2)
        batch.repair_ids[0].serial_number = 'SN123'
        batch.repair_ids[1].serial_number = 'SN456'
        apt = self.Appointment.create({'batch_id': batch.id})
        self.assertEqual(apt.device_count, 2)
        self.assertIn('SN123', apt.device_summary)
        self.assertIn('SN456', apt.device_summary)
