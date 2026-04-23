from datetime import date, datetime, timedelta
from unittest.mock import patch
from odoo import fields
from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestReminderCron(RepairAppointmentCase):

    def _make_pending_with_notification(self, days_ago=0):
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        apt.notification_sent_at = fields.Datetime.now() - timedelta(days=days_ago)
        return apt

    def test_no_reminder_if_notification_too_recent(self):
        apt = self._make_pending_with_notification(days_ago=1)
        self.Appointment._cron_process_pending_appointments()
        self.assertFalse(apt.last_reminder_sent_at)

    def test_reminder_sent_after_delay(self):
        apt = self._make_pending_with_notification(days_ago=4)
        self.Appointment._cron_process_pending_appointments()
        self.assertTrue(apt.last_reminder_sent_at)

    def test_no_second_reminder(self):
        """Only 1 reminder is ever sent (user chose option i in Q10a)."""
        apt = self._make_pending_with_notification(days_ago=4)
        self.Appointment._cron_process_pending_appointments()
        stamp_1 = apt.last_reminder_sent_at
        self.Appointment._cron_process_pending_appointments()
        self.assertEqual(apt.last_reminder_sent_at, stamp_1)

    def test_escalation_after_reminder_delay(self):
        apt = self._make_pending_with_notification(days_ago=4)
        self.Appointment._cron_process_pending_appointments()
        # Manually back-date last_reminder_sent_at by 4 days
        apt.last_reminder_sent_at = fields.Datetime.now() - timedelta(days=4)
        self.Appointment._cron_process_pending_appointments()
        apt.invalidate_recordset(['escalation_activity_id'])
        self.assertTrue(apt.escalation_activity_id)

    def test_no_escalation_if_state_no_longer_pending(self):
        apt = self._make_pending_with_notification(days_ago=4)
        self.Appointment._cron_process_pending_appointments()
        apt.last_reminder_sent_at = fields.Datetime.now() - timedelta(days=4)
        apt.with_context(skip_slot_validation=True).action_schedule(
            date.today() + timedelta(days=3),
        )
        self.Appointment._cron_process_pending_appointments()
        self.assertFalse(apt.escalation_activity_id)

    def test_contacted_reset_cycle(self):
        apt = self._make_pending_with_notification(days_ago=4)
        self.Appointment._cron_process_pending_appointments()
        apt.last_reminder_sent_at = fields.Datetime.now() - timedelta(days=4)
        self.Appointment._cron_process_pending_appointments()
        apt.invalidate_recordset(['escalation_activity_id'])
        self.assertTrue(apt.escalation_activity_id)
        apt.action_mark_contacted()
        apt.invalidate_recordset(['escalation_activity_id'])
        self.assertFalse(apt.escalation_activity_id)
        self.assertTrue(apt.contacted)
        # Back-date contacted_at beyond escalation delay
        apt.contacted_at = fields.Datetime.now() - timedelta(days=4)
        self.Appointment._cron_process_pending_appointments()
        apt.invalidate_recordset(['escalation_activity_id'])
        self.assertTrue(apt.escalation_activity_id)
        self.assertFalse(apt.contacted)  # flag consumed
