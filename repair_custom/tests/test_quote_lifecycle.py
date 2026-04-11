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


class TestSaleOrderSync(RepairQuoteCase):
    """Tests for sale.order.write() override syncing to repair.quote_state."""

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()
        self.repair._apply_quote_state_transition('pending')
        self.sale_order = self._make_sale_order_linked(self.repair)
        # sale.order is created in 'draft', repair stays in 'pending'

    def test_sale_order_draft_to_sent_syncs_to_sent(self):
        self.sale_order.state = 'sent'
        self.assertEqual(self.repair.quote_state, 'sent')
        self.assertTrue(self.repair.quote_sent_date)

    def test_sale_order_sent_to_sale_syncs_to_approved(self):
        self.sale_order.state = 'sent'
        self.sale_order.state = 'sale'
        self.assertEqual(self.repair.quote_state, 'approved')

    def test_sale_order_sent_to_cancel_syncs_to_refused(self):
        self.sale_order.state = 'sent'
        self.sale_order.state = 'cancel'
        self.assertEqual(self.repair.quote_state, 'refused')

    def test_cancel_then_draft_syncs_back_to_pending(self):
        self.sale_order.state = 'sent'
        self.sale_order.state = 'cancel'
        self.sale_order.state = 'draft'
        self.assertEqual(self.repair.quote_state, 'pending')

    def test_sale_order_without_repair_link_no_op(self):
        solo_so = self.SaleOrder.create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.service_product.id,
                'name': 'Standalone',
                'product_uom_qty': 1.0,
                'price_unit': 10.0,
            })],
        })
        # Should not crash
        solo_so.state = 'sent'
        solo_so.state = 'sale'

    def test_same_state_write_is_idempotent(self):
        self.sale_order.state = 'sent'
        before_count = len(self.repair.message_ids)
        self.sale_order.write({'state': 'sent'})
        self.assertEqual(len(self.repair.message_ids), before_count,
                         "Re-writing same state must not re-fire side effects")


class TestComputedActivityFlags(RepairQuoteCase):
    """Tests for has_open_escalation and has_open_refusal_activity."""

    def test_has_open_escalation_false_by_default(self):
        repair = self._make_repair()
        self.assertFalse(repair.has_open_escalation)

    def test_has_open_escalation_true_when_activity_open(self):
        repair = self._make_repair()
        escalate_type = self.env.ref('repair_custom.mail_act_repair_quote_escalate')
        repair.activity_schedule(
            activity_type_id=escalate_type.id,
            user_id=self.manager_user_1.id,
            summary='Test',
        )
        repair.invalidate_recordset(['has_open_escalation'])
        self.assertTrue(repair.has_open_escalation)

    def test_has_open_escalation_false_when_activity_done(self):
        repair = self._make_repair()
        escalate_type = self.env.ref('repair_custom.mail_act_repair_quote_escalate')
        activity = repair.activity_schedule(
            activity_type_id=escalate_type.id,
            user_id=self.manager_user_1.id,
            summary='Test',
        )
        activity.action_feedback(feedback='done')
        repair.invalidate_recordset(['has_open_escalation'])
        self.assertFalse(repair.has_open_escalation)

    def test_has_open_refusal_activity_true_after_refusal(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('sent')
        repair._apply_quote_state_transition('refused')
        repair.invalidate_recordset(['has_open_refusal_activity'])
        self.assertTrue(repair.has_open_refusal_activity)


class TestActionQuoteContacted(RepairQuoteCase):
    """Tests for the 'Contacté' button handler."""

    def _setup_sent_with_escalation(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('sent')
        escalate_type = self.env.ref('repair_custom.mail_act_repair_quote_escalate')
        # Create one activity per manager (mirrors the CRON behaviour)
        for manager in [self.manager_user_1, self.manager_user_2]:
            repair.activity_schedule(
                activity_type_id=escalate_type.id,
                user_id=manager.id,
                summary='Test escalate',
            )
        repair.invalidate_recordset(['has_open_escalation'])
        return repair

    def test_contacted_closes_all_sibling_activities(self):
        repair = self._setup_sent_with_escalation()
        self.assertTrue(repair.has_open_escalation)
        repair.action_quote_contacted()
        repair.invalidate_recordset(['has_open_escalation'])
        self.assertFalse(repair.has_open_escalation)

    def test_contacted_sets_contacted_flag_and_timestamp(self):
        repair = self._setup_sent_with_escalation()
        repair.action_quote_contacted()
        self.assertTrue(repair.contacted)
        self.assertTrue(repair.contacted_at)

    def test_contacted_posts_chatter_note(self):
        repair = self._setup_sent_with_escalation()
        before = len(repair.message_ids)
        repair.action_quote_contacted()
        self.assertGreater(len(repair.message_ids), before)
