from datetime import datetime, date, timedelta
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user
from .common import RepairAppointmentCase


@tagged('repair_appointment', 'post_install', '-at_install')
class TestPortalController(HttpCase, RepairAppointmentCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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

    def test_book_valid_slot_redirects_to_confirmation(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        # Find an open future day that isn't Sunday
        target = date.today() + timedelta(days=3)
        while target.weekday() == 6:
            target += timedelta(days=1)
        start = datetime.combine(target, datetime.min.time()).replace(hour=15)
        resp = self.url_open(
            f'/my/pickup/{apt.token}/book',
            data={'start_datetime': start.isoformat()},
            allow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertIn('confirmation', resp.headers.get('Location', ''))
        apt.invalidate_recordset()
        self.assertEqual(apt.state, 'scheduled')

    def test_book_rejected_for_invalid_state(self):
        batch = self._make_batch()
        apt = batch.action_create_pickup_appointment(notify=False)
        apt.action_cancel()
        target = date.today() + timedelta(days=3)
        while target.weekday() == 6:
            target += timedelta(days=1)
        start = datetime.combine(target, datetime.min.time()).replace(hour=15)
        resp = self.url_open(
            f'/my/pickup/{apt.token}/book',
            data={'start_datetime': start.isoformat()},
        )
        # Should render the page with an error (not a redirect)
        self.assertEqual(resp.status_code, 200)
        apt.invalidate_recordset()
        self.assertEqual(apt.state, 'cancelled')
