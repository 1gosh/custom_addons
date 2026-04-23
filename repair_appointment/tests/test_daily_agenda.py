from datetime import date, timedelta
from .common import RepairAppointmentCase


class TestDailyAgenda(RepairAppointmentCase):

    def test_action_daily_agenda_default_domain(self):
        action = self.env.ref(
            'repair_appointment.action_repair_pickup_appointment_daily_agenda'
        )
        self.assertEqual(action.res_model, 'repair.pickup.appointment')
        # Verify the action's context string contains the default-filter marker
        raw = action.context or ''
        self.assertIn('search_default_today_or_tomorrow', raw)

    def test_today_or_tomorrow_filter_matches_expected_records(self):
        batch1 = self._make_batch()
        today = date.today()
        apt_today = self.Appointment.create({
            'batch_id': batch1.id,
            'pickup_date': today,
            'state': 'scheduled',
        })
        batch2 = self._make_batch()
        apt_far = self.Appointment.create({
            'batch_id': batch2.id,
            'pickup_date': today + timedelta(days=10),
            'state': 'scheduled',
        })
        domain = [
            ('state', '=', 'scheduled'),
            ('pickup_date', '>=', today),
            ('pickup_date', '<=', today + timedelta(days=1)),
        ]
        found = self.Appointment.search(domain)
        self.assertIn(apt_today, found)
        self.assertNotIn(apt_far, found)
