from datetime import date
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestClosure(RepairAppointmentCase):

    def test_create_closure_specific_location(self):
        closure = self.Closure.create({
            'name': 'Congés août',
            'location_id': self.location_boutique.id,
            'date_from': date(2026, 8, 1),
            'date_to': date(2026, 8, 20),
        })
        self.assertTrue(closure.active)

    def test_create_closure_all_locations(self):
        closure = self.Closure.create({
            'name': 'Férié national',
            'date_from': date(2026, 5, 1),
            'date_to': date(2026, 5, 1),
        })
        self.assertFalse(closure.location_id)

    def test_date_to_must_be_on_or_after_date_from(self):
        with self.assertRaises(ValidationError):
            self.Closure.create({
                'name': 'Oops',
                'date_from': date(2026, 5, 10),
                'date_to': date(2026, 5, 1),
            })

    def test_covers_date_specific_location(self):
        closure = self.Closure.create({
            'name': 'Congés',
            'location_id': self.location_boutique.id,
            'date_from': date(2026, 8, 1),
            'date_to': date(2026, 8, 10),
        })
        self.assertTrue(closure._covers(date(2026, 8, 5), self.location_boutique))
        self.assertFalse(closure._covers(date(2026, 8, 5), self.location_atelier))
        self.assertFalse(closure._covers(date(2026, 8, 15), self.location_boutique))

    def test_covers_date_all_locations(self):
        closure = self.Closure.create({
            'name': 'Férié',
            'date_from': date(2026, 5, 1),
            'date_to': date(2026, 5, 1),
        })
        self.assertTrue(closure._covers(date(2026, 5, 1), self.location_boutique))
        self.assertTrue(closure._covers(date(2026, 5, 1), self.location_atelier))
