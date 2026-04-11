# -*- coding: utf-8 -*-
from .common import RepairQuoteCase


class TestQuoteLifecycleBootstrap(RepairQuoteCase):
    """Sanity check that the test fixture loads."""

    def test_fixture_loads(self):
        self.assertTrue(self.partner)
        self.assertTrue(self.manager_group)
        self.assertEqual(len(self.manager_group.users.filtered(
            lambda u: u.login.startswith('manager') and 'quote_test' in u.login
        )), 2)
