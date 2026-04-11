from odoo.exceptions import ValidationError
from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestSchedule(RepairAppointmentCase):

    def test_create_schedule_with_defaults(self):
        schedule = self.Schedule.create({
            'location_id': self.location_boutique.id,
        })
        self.assertTrue(schedule.monday_open)
        self.assertTrue(schedule.saturday_open)
        self.assertFalse(schedule.sunday_open)
        self.assertEqual(schedule.slot_capacity, 3)
        self.assertAlmostEqual(schedule.slot1_start, 15.0)
        self.assertAlmostEqual(schedule.slot1_end, 17.25)
        self.assertAlmostEqual(schedule.slot2_start, 17.25)
        self.assertAlmostEqual(schedule.slot2_end, 19.5)

    def test_unique_schedule_per_location(self):
        self.Schedule.create({'location_id': self.location_boutique.id})
        with self.assertRaises(Exception):
            self.Schedule.create({'location_id': self.location_boutique.id})

    def test_slot1_must_end_after_start(self):
        with self.assertRaises(ValidationError):
            self.Schedule.create({
                'location_id': self.location_boutique.id,
                'slot1_start': 17.0,
                'slot1_end': 15.0,
            })

    def test_slot2_must_not_overlap_slot1(self):
        with self.assertRaises(ValidationError):
            self.Schedule.create({
                'location_id': self.location_boutique.id,
                'slot1_start': 15.0,
                'slot1_end': 17.25,
                'slot2_start': 16.0,  # overlaps slot1
                'slot2_end': 18.0,
            })
