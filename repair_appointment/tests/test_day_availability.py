from datetime import date, timedelta
from .common import RepairAppointmentCase


class TestDayAvailability(RepairAppointmentCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schedule_boutique = cls.Schedule.search(
            [('location_id', '=', cls.location_boutique.id)], limit=1
        ) or cls.Schedule.create({'location_id': cls.location_boutique.id})

    def _days(self, date_from=None, date_to=None):
        return self.Appointment._compute_available_days(
            self.location_boutique, date_from=date_from, date_to=date_to,
        )

    def test_sunday_is_closed(self):
        # Find a Sunday within the horizon
        today = date.today()
        for offset in range(2, 20):
            d = today + timedelta(days=offset)
            if d.weekday() == 6:
                break
        days = {x['date']: x for x in self._days()}
        self.assertIn(d, days)
        self.assertEqual(days[d]['state'], 'closed')

    def test_closure_covers_day(self):
        target = date.today() + timedelta(days=3)
        self.Closure.create({
            'name': 'Test closure',
            'date_from': target, 'date_to': target,
        })
        days = {x['date']: x for x in self._days()}
        self.assertEqual(days[target]['state'], 'closed')

    def test_min_lead_time_respected(self):
        too_soon = date.today() + timedelta(days=1)
        days = {x['date']: x for x in self._days()}
        self.assertNotIn(too_soon, days)

    def test_horizon_respected(self):
        beyond = date.today() + timedelta(days=30)
        days = {x['date']: x for x in self._days()}
        self.assertNotIn(beyond, days)

    def test_daily_cap_full_renders_full_state(self):
        target = date.today() + timedelta(days=3)
        # Make sure it's an open weekday
        while target.weekday() == 6:
            target += timedelta(days=1)
        self.schedule_boutique.daily_capacity = 1
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        apt.with_context(skip_slot_validation=True).action_schedule(target)
        days = {x['date']: x for x in self._days()}
        self.assertEqual(days[target]['state'], 'full')
        self.assertEqual(days[target]['remaining_capacity'], 0)

    def test_is_day_available_false_for_closed_day(self):
        today = date.today()
        for offset in range(2, 20):
            d = today + timedelta(days=offset)
            if d.weekday() == 6:
                break
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        self.assertFalse(apt._is_day_available(d))
