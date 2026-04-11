from datetime import datetime, timedelta
from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestRescheduleNotification(RepairAppointmentCase):

    def _make_scheduled(self):
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        start = datetime.now().replace(
            hour=15, minute=0, second=0, microsecond=0,
        ) + timedelta(days=3)
        end = start + timedelta(hours=2, minutes=15)
        apt.with_context(skip_slot_validation=True).action_schedule(start, end)
        return apt, start

    def test_write_datetime_sends_reschedule_mail(self):
        apt, start = self._make_scheduled()
        before = self.env['mail.mail'].search_count([])
        new_start = start + timedelta(days=1)
        new_end = new_start + timedelta(hours=2, minutes=15)
        apt.with_context(bypass_capacity=True).write({
            'start_datetime': new_start,
            'end_datetime': new_end,
        })
        self.assertGreater(
            self.env['mail.mail'].search_count([]),
            before,
            "Reschedule should have queued a mail.mail record",
        )

    def test_write_datetime_with_skip_context_no_mail(self):
        apt, start = self._make_scheduled()
        before = self.env['mail.mail'].search_count([])
        new_start = start + timedelta(days=2)
        new_end = new_start + timedelta(hours=2, minutes=15)
        apt.with_context(
            skip_reschedule_notification=True,
            bypass_capacity=True,
        ).write({
            'start_datetime': new_start,
            'end_datetime': new_end,
        })
        self.assertEqual(
            self.env['mail.mail'].search_count([]),
            before,
            "skip_reschedule_notification must suppress the mail send",
        )

    def test_write_other_field_does_not_trigger_mail(self):
        apt, _start = self._make_scheduled()
        before = self.env['mail.mail'].search_count([])
        apt.write({'contacted': True})
        self.assertEqual(
            self.env['mail.mail'].search_count([]),
            before,
            "Writes that do not touch start_datetime must not send mail",
        )

    def test_write_first_time_scheduling_does_not_trigger_reschedule_mail(self):
        """A pending->scheduled transition with no prior start_datetime must
        NOT trigger the reschedule mail -- that mail says 'deplace' which is
        wrong for a first-time booking."""
        batch = self._make_batch()
        pending_apt = self.env['repair.pickup.appointment'].create({
            'batch_id': batch.id,
            'state': 'pending',
        })
        before = self.env['mail.mail'].search_count([])
        pending_apt.with_context(skip_slot_validation=True).write({
            'state': 'scheduled',
            'start_datetime': datetime(2026, 5, 5, 10, 0),
            'end_datetime': datetime(2026, 5, 5, 10, 30),
        })
        self.assertEqual(
            self.env['mail.mail'].search_count([]),
            before,
            "First-time scheduling must not send a reschedule (deplace) mail",
        )
