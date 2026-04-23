from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestEscalation(RepairAppointmentCase):

    def _manager_users(self):
        return self.env.ref('repair_custom.group_repair_manager').users

    def test_create_escalation_activity_per_manager(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        managers = self._manager_users()
        self.assertTrue(managers, "Need at least one manager user in fixtures")
        apt._create_escalation_activity()
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'repair.pickup.appointment'),
            ('res_id', '=', apt.id),
        ])
        self.assertEqual(len(activities), len(managers))
        self.assertEqual(
            set(activities.mapped('user_id.id')),
            set(managers.ids),
        )

    def test_escalation_activity_id_compute(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        self.assertFalse(apt.escalation_activity_id)
        apt._create_escalation_activity()
        apt.invalidate_recordset(['escalation_activity_id'])
        self.assertTrue(apt.escalation_activity_id)

    def test_action_mark_contacted_closes_all_sibling_activities(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        apt._create_escalation_activity()
        apt.action_mark_contacted()
        remaining = self.env['mail.activity'].search([
            ('res_model', '=', 'repair.pickup.appointment'),
            ('res_id', '=', apt.id),
        ])
        self.assertFalse(remaining)
        self.assertTrue(apt.contacted)
        self.assertTrue(apt.contacted_at)

    def test_action_schedule_closes_escalation_activities(self):
        from datetime import date, timedelta
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        apt._create_escalation_activity()
        apt.with_context(skip_slot_validation=True).action_schedule(
            date.today() + timedelta(days=3),
        )
        remaining = self.env['mail.activity'].search([
            ('res_model', '=', 'repair.pickup.appointment'),
            ('res_id', '=', apt.id),
        ])
        self.assertFalse(remaining)
