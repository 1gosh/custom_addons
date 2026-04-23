from datetime import date, timedelta
from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestRescheduleNotification(RepairAppointmentCase):

    def _make_scheduled(self):
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        d = date.today() + timedelta(days=3)
        apt.with_context(skip_slot_validation=True).action_schedule(d)
        return apt, d

    def test_write_pickup_date_sends_reschedule_mail(self):
        apt, d = self._make_scheduled()
        before = self.env['mail.mail'].search_count([])
        new_date = d + timedelta(days=1)
        apt.write({'pickup_date': new_date})
        self.assertGreater(
            self.env['mail.mail'].search_count([]),
            before,
            "Reschedule should have queued a mail.mail record",
        )

    def test_write_pickup_date_with_skip_context_no_mail(self):
        apt, d = self._make_scheduled()
        before = self.env['mail.mail'].search_count([])
        new_date = d + timedelta(days=2)
        apt.with_context(skip_reschedule_notification=True).write({
            'pickup_date': new_date,
        })
        self.assertEqual(
            self.env['mail.mail'].search_count([]),
            before,
            "skip_reschedule_notification must suppress the mail send",
        )

    def test_write_other_field_does_not_trigger_mail(self):
        apt, _d = self._make_scheduled()
        before = self.env['mail.mail'].search_count([])
        apt.write({'contacted': True})
        self.assertEqual(
            self.env['mail.mail'].search_count([]),
            before,
            "Writes that do not touch pickup_date must not send mail",
        )

    def test_write_first_time_scheduling_does_not_trigger_reschedule_mail(self):
        """A pending->scheduled transition with no prior pickup_date must
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
            'pickup_date': date(2026, 5, 5),
        })
        self.assertEqual(
            self.env['mail.mail'].search_count([]),
            before,
            "First-time scheduling must not send a reschedule (deplace) mail",
        )
