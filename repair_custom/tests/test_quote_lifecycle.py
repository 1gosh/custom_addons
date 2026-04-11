# -*- coding: utf-8 -*-
from odoo import fields
from odoo.exceptions import UserError

from .common import RepairQuoteCase


class TestQuoteLifecycleBootstrap(RepairQuoteCase):
    """Sanity check that the test fixture loads."""

    def test_fixture_loads(self):
        self.assertTrue(self.partner)
        self.assertTrue(self.manager_group)


class TestQuoteStateTransitions(RepairQuoteCase):
    """Tests for the _apply_quote_state_transition entry point."""

    def test_transition_none_to_pending_sets_state(self):
        repair = self._make_repair()
        self.assertEqual(repair.quote_state, 'none')
        repair._apply_quote_state_transition('pending')
        self.assertEqual(repair.quote_state, 'pending')

    def test_transition_pending_to_sent_sets_sent_date(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('pending')
        repair._apply_quote_state_transition('sent')
        self.assertEqual(repair.quote_state, 'sent')
        self.assertTrue(repair.quote_sent_date, "quote_sent_date must be set on entry to 'sent'")

    def test_transition_sent_to_approved_posts_chatter_note(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('sent')
        before_count = len(repair.message_ids)
        repair._apply_quote_state_transition('approved')
        self.assertEqual(repair.quote_state, 'approved')
        self.assertGreater(len(repair.message_ids), before_count,
                           "A chatter message should be posted on approval")
        latest = repair.message_ids[0]
        self.assertIn('validé', (latest.body or '').lower())

    def test_transition_sent_to_refused_creates_refusal_activity(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('sent')
        repair._apply_quote_state_transition('refused')
        self.assertEqual(repair.quote_state, 'refused')
        refusal_type = self.env.ref('repair_custom.mail_act_repair_quote_refused')
        refusal_activities = repair.activity_ids.filtered(
            lambda a: a.activity_type_id == refusal_type and a.state != 'done'
        )
        self.assertTrue(refusal_activities,
                        "At least one refusal activity should be created")

    def test_transition_noop_on_same_state(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('pending')
        before_count = len(repair.message_ids)
        repair._apply_quote_state_transition('pending')
        self.assertEqual(len(repair.message_ids), before_count,
                         "Re-applying the same state must not post a new message")

    def test_transition_back_to_pending_posts_chatter_note(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('sent')
        before_count = len(repair.message_ids)
        repair._apply_quote_state_transition('pending')
        self.assertGreater(len(repair.message_ids), before_count,
                           "Going back to pending from sent should post a chatter note")


class TestActionRequestQuote(RepairQuoteCase):
    """Tests for the refactored action_atelier_request_quote."""

    def test_request_quote_transitions_to_pending(self):
        repair = self._make_repair(tech=self.tech_with_user)
        repair.action_atelier_request_quote()
        self.assertEqual(repair.quote_state, 'pending')

    def test_request_quote_sets_quote_requested_date(self):
        repair = self._make_repair()
        repair.action_atelier_request_quote()
        self.assertTrue(repair.quote_requested_date)

    def test_request_quote_creates_no_legacy_activities(self):
        repair = self._make_repair()
        repair.action_atelier_request_quote()
        legacy_type = self.env.ref('repair_custom.mail_act_repair_quote_validate')
        legacy_activities = repair.activity_ids.filtered(
            lambda a: a.activity_type_id == legacy_type
        )
        self.assertFalse(
            legacy_activities,
            "action_atelier_request_quote must not create per-manager activities anymore"
        )

    def test_request_quote_requires_internal_notes(self):
        repair = self._make_repair(internal_notes=False)
        with self.assertRaises(UserError):
            repair.action_atelier_request_quote()

    def test_request_quote_posts_chatter_note(self):
        repair = self._make_repair()
        before = len(repair.message_ids)
        repair.action_atelier_request_quote()
        self.assertGreater(len(repair.message_ids), before)
