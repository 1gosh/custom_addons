from datetime import date, timedelta
from odoo.tests import HttpCase, tagged
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestPortalController(HttpCase, RepairAppointmentCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Drop any schedule the tenant may have created manually via the
        # backend so the test owns its fixture. Safe because the class
        # savepoint rolls this back at teardown.
        cls.Schedule.search([]).unlink()
        cls.Schedule._seed_default_schedules()

    def test_landing_valid_token(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        resp = self.url_open(f'/my/pickup/{apt.token}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(apt.batch_id.name, resp.text)

    def test_landing_invalid_token_404(self):
        resp = self.url_open('/my/pickup/not-a-real-token')
        self.assertEqual(resp.status_code, 404)

    def test_book_valid_date_redirects_to_confirmation(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        # Find an open future day that isn't Sunday
        target = date.today() + timedelta(days=3)
        while target.weekday() == 6:
            target += timedelta(days=1)
        resp = self.url_open(
            f'/my/pickup/{apt.token}/book',
            data={'pickup_date': target.isoformat()},
            allow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertIn('confirmation', resp.headers.get('Location', ''))
        apt.invalidate_recordset()
        self.assertEqual(apt.state, 'scheduled')
        self.assertEqual(apt.pickup_date, target)

    def test_book_rejected_for_invalid_state(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        apt.action_cancel()
        target = date.today() + timedelta(days=3)
        while target.weekday() == 6:
            target += timedelta(days=1)
        resp = self.url_open(
            f'/my/pickup/{apt.token}/book',
            data={'pickup_date': target.isoformat()},
        )
        # Should render the page with an error (not a redirect)
        self.assertEqual(resp.status_code, 200)
        apt.invalidate_recordset()
        self.assertEqual(apt.state, 'cancelled')

    def test_book_missing_date_returns_error_page(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        resp = self.url_open(
            f'/my/pickup/{apt.token}/book',
            data={},
        )
        self.assertEqual(resp.status_code, 200)
        apt.invalidate_recordset()
        self.assertEqual(apt.state, 'pending')

    def test_reschedule_valid_date_redirects_to_confirmation(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        # First book a date so state becomes 'scheduled'
        first_target = date.today() + timedelta(days=3)
        while first_target.weekday() == 6:
            first_target += timedelta(days=1)
        apt.sudo().action_schedule(first_target)
        self.assertEqual(apt.state, 'scheduled')

        new_target = first_target + timedelta(days=7)
        while new_target.weekday() == 6:
            new_target += timedelta(days=1)
        resp = self.url_open(
            f'/my/pickup/{apt.token}/reschedule',
            data={'pickup_date': new_target.isoformat()},
            allow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertIn('confirmation', resp.headers.get('Location', ''))
        apt.invalidate_recordset()
        self.assertEqual(apt.pickup_date, new_target)

    def test_slots_endpoint_returns_per_day_objects(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        # Invoke the compute method directly (bypasses HTTP layer)
        days = self.Appointment._compute_available_days(apt.location_id)
        self.assertTrue(days)
        sample = days[0]
        self.assertIn('date', sample)
        self.assertIn('state', sample)
        self.assertIn('remaining_capacity', sample)
        self.assertIn(sample['state'],
                      ('open', 'closed', 'full', 'lead_time'))
