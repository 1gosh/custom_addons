from datetime import datetime, date, timedelta
from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestSlotAvailability(RepairAppointmentCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Drop any schedule the tenant may have created manually via the
        # backend so the test owns its fixture. Safe because the class
        # savepoint rolls this back at teardown.
        cls.Schedule.search([]).unlink()
        cls.Schedule._seed_default_schedules()

    def _count_available(self, slots):
        return sum(1 for s in slots if s['remaining_capacity'] > 0)

    def test_slots_respect_weekly_schedule(self):
        """Sunday must never be returned as available."""
        today = date.today()
        # Find the next Sunday, at least J+2
        offset = (6 - today.weekday()) % 7
        if offset < 2:
            offset += 7
        target_sunday = today + timedelta(days=offset)
        slots = self.Appointment._compute_available_slots(
            self.location_boutique,
            date_from=target_sunday,
            date_to=target_sunday,
        )
        self.assertEqual(slots, [])

    def test_slots_respect_closure(self):
        target = date.today() + timedelta(days=5)
        # Skip if target is Sunday
        if target.weekday() == 6:
            target += timedelta(days=1)
        self.Closure.create({
            'name': 'Test closure',
            'location_id': self.location_boutique.id,
            'date_from': target,
            'date_to': target,
        })
        slots = self.Appointment._compute_available_slots(
            self.location_boutique,
            date_from=target,
            date_to=target,
        )
        self.assertEqual(slots, [])

    def test_slots_respect_min_lead_time(self):
        """Today and tomorrow must not appear."""
        slots = self.Appointment._compute_available_slots(
            self.location_boutique,
            date_from=date.today(),
            date_to=date.today() + timedelta(days=1),
        )
        self.assertEqual(slots, [])

    def test_slots_horizon(self):
        """Slots beyond booking horizon are not returned."""
        far_day = date.today() + timedelta(days=30)
        slots = self.Appointment._compute_available_slots(
            self.location_boutique,
            date_from=far_day,
            date_to=far_day,
            booking_horizon_days=14,
        )
        self.assertEqual(slots, [])

    def test_slots_open_day_returns_two_slots(self):
        """A non-Sunday, non-closure, post-J+2 day yields 2 slots."""
        target = date.today() + timedelta(days=3)
        # Avoid Sunday
        if target.weekday() == 6:
            target += timedelta(days=1)
        slots = self.Appointment._compute_available_slots(
            self.location_boutique,
            date_from=target,
            date_to=target,
        )
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0]['remaining_capacity'], 3)
        self.assertEqual(slots[1]['remaining_capacity'], 3)

    def test_capacity_counting_excludes_cancelled(self):
        target = date.today() + timedelta(days=3)
        if target.weekday() == 6:
            target += timedelta(days=1)
        slot_start = datetime.combine(target, datetime.min.time()).replace(hour=15)
        slot_end = slot_start + timedelta(hours=2, minutes=15)
        # Create a cancelled appointment in that slot
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        apt.with_context(skip_slot_validation=True).action_schedule(slot_start, slot_end)
        apt.action_cancel()
        # Should still show 3 capacity
        count = self.Appointment._count_booked_in_slot(slot_start, self.location_boutique)
        self.assertEqual(count, 0)

    def test_capacity_counting_includes_scheduled(self):
        target = date.today() + timedelta(days=3)
        if target.weekday() == 6:
            target += timedelta(days=1)
        slot_start = datetime.combine(target, datetime.min.time()).replace(hour=15)
        slot_end = slot_start + timedelta(hours=2, minutes=15)
        batch = self._make_batch()
        apt = self.Appointment.create({'batch_id': batch.id})
        apt.with_context(skip_slot_validation=True).action_schedule(slot_start, slot_end)
        count = self.Appointment._count_booked_in_slot(slot_start, self.location_boutique)
        self.assertEqual(count, 1)
