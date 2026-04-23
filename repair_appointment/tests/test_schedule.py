from odoo.exceptions import ValidationError
from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestSchedule(RepairAppointmentCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Clear any schedules created by the install-time seed function
        # so each test owns its fixture. Safe because the class-level
        # savepoint rolls this back at class teardown.
        cls.Schedule.search([]).unlink()

    def test_create_schedule_with_defaults(self):
        schedule = self.Schedule.create({
            'location_id': self.location_boutique.id,
        })
        self.assertTrue(schedule.monday_open)
        self.assertTrue(schedule.saturday_open)
        self.assertFalse(schedule.sunday_open)

    def test_unique_schedule_per_location(self):
        self.Schedule.create({'location_id': self.location_boutique.id})
        with self.assertRaises(Exception):
            self.Schedule.create({'location_id': self.location_boutique.id})

    def test_daily_capacity_field_present_and_default(self):
        sched = self.Schedule.create({'location_id': self.location_boutique.id})
        self.assertEqual(sched.daily_capacity, 6)

    def test_daily_capacity_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self.Schedule.create({
                'location_id': self.location_atelier.id,
                'daily_capacity': 0,
            })

    def test_old_slot_fields_removed(self):
        sched = self.Schedule.create({'location_id': self.location_boutique.id})
        for fname in ('slot1_start', 'slot1_end', 'slot2_start',
                      'slot2_end', 'slot_capacity'):
            self.assertNotIn(fname, sched._fields,
                             "%s must be removed" % fname)
