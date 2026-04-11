# -*- coding: utf-8 -*-
from datetime import timedelta

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


class TestReminderMail(RepairQuoteCase):
    """Test the reminder mail helper in isolation."""

    def test_send_quote_reminder_mail_posts_message(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('sent')
        before = len(repair.message_ids)
        repair._send_quote_reminder_mail()
        self.assertGreater(len(repair.message_ids), before,
                           "Sending the reminder should post a tracked message on the repair")


class TestCronReminderPhase(RepairQuoteCase):
    """Tests for the reminder phase of _cron_process_pending_quotes."""

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()
        self.repair._apply_quote_state_transition('sent')

    def _rewind_sent_date(self, days):
        self.repair.quote_sent_date = fields.Datetime.now() - timedelta(days=days)

    def test_reminder_not_sent_before_delay(self):
        self._rewind_sent_date(4)  # reminder_delay default is 5
        self.Repair._cron_process_pending_quotes()
        self.assertFalse(self.repair.last_reminder_sent_at)

    def test_reminder_sent_after_delay(self):
        self._rewind_sent_date(5)
        self.Repair._cron_process_pending_quotes()
        self.assertTrue(self.repair.last_reminder_sent_at)

    def test_reminder_sent_only_once(self):
        self._rewind_sent_date(5)
        self.Repair._cron_process_pending_quotes()
        first_timestamp = self.repair.last_reminder_sent_at
        self.Repair._cron_process_pending_quotes()
        self.assertEqual(self.repair.last_reminder_sent_at, first_timestamp,
                         "CRON must not send a second reminder mail")

    def test_cron_ignores_repairs_without_sent_date(self):
        self.repair.quote_sent_date = False
        # Should not crash
        self.Repair._cron_process_pending_quotes()
        self.assertFalse(self.repair.last_reminder_sent_at)

    def test_cron_ignores_non_sent_state(self):
        self.repair._apply_quote_state_transition('approved')
        self._rewind_sent_date(10)
        self.Repair._cron_process_pending_quotes()
        self.assertFalse(self.repair.last_reminder_sent_at)


class TestCronEscalationPhase(RepairQuoteCase):
    """Tests for the escalation phase of _cron_process_pending_quotes."""

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()
        self.repair._apply_quote_state_transition('sent')
        # Pretend the reminder was already sent 4 days ago
        self.repair.quote_sent_date = fields.Datetime.now() - timedelta(days=10)
        self.repair.last_reminder_sent_at = fields.Datetime.now() - timedelta(days=4)

    def test_escalation_not_created_before_delay(self):
        self.repair.last_reminder_sent_at = fields.Datetime.now() - timedelta(days=2)
        self.Repair._cron_process_pending_quotes()
        self.repair.invalidate_recordset(['has_open_escalation'])
        self.assertFalse(self.repair.has_open_escalation)

    def test_escalation_created_after_delay(self):
        self.Repair._cron_process_pending_quotes()
        self.repair.invalidate_recordset(['has_open_escalation'])
        self.assertTrue(self.repair.has_open_escalation)

    def test_one_escalation_activity_per_manager(self):
        self.Repair._cron_process_pending_quotes()
        escalate_type = self.env.ref('repair_custom.mail_act_repair_quote_escalate')
        activities = self.repair.activity_ids.filtered(
            lambda a: a.activity_type_id == escalate_type and a.state != 'done'
        )
        # Expect one per user in group_repair_manager
        expected_count = len(self.env.ref('repair_custom.group_repair_manager').users)
        self.assertEqual(len(activities), expected_count)

    def test_escalation_not_recreated_if_already_open(self):
        self.Repair._cron_process_pending_quotes()
        escalate_type = self.env.ref('repair_custom.mail_act_repair_quote_escalate')
        first_count = len(self.repair.activity_ids.filtered(
            lambda a: a.activity_type_id == escalate_type and a.state != 'done'
        ))
        # Run CRON again
        self.Repair._cron_process_pending_quotes()
        second_count = len(self.repair.activity_ids.filtered(
            lambda a: a.activity_type_id == escalate_type and a.state != 'done'
        ))
        self.assertEqual(first_count, second_count)

    def test_contacted_resets_escalation_clock(self):
        # First escalation
        self.Repair._cron_process_pending_quotes()
        # Manager clicks "Contacté"
        self.repair.action_quote_contacted()
        self.repair.invalidate_recordset(['has_open_escalation'])
        self.assertFalse(self.repair.has_open_escalation)
        # Rewind contacted_at
        self.repair.contacted_at = fields.Datetime.now() - timedelta(days=2)
        # CRON should not re-escalate yet
        self.Repair._cron_process_pending_quotes()
        self.repair.invalidate_recordset(['has_open_escalation'])
        self.assertFalse(self.repair.has_open_escalation)
        # After 3 days, escalation fires again
        self.repair.contacted_at = fields.Datetime.now() - timedelta(days=3)
        self.Repair._cron_process_pending_quotes()
        self.repair.invalidate_recordset(['has_open_escalation'])
        self.assertTrue(self.repair.has_open_escalation)
        # contacted flag is consumed
        self.assertFalse(self.repair.contacted)


class TestQuoteLifecycleIntegration(RepairQuoteCase):
    """End-to-end: tech request → manager creates SO → client portal accept."""

    def test_full_happy_path_via_sale_order_sync(self):
        # Tech requests quote
        repair = self._make_repair(tech=self.tech_with_user)
        repair.action_atelier_request_quote()
        self.assertEqual(repair.quote_state, 'pending')
        self.assertTrue(repair.quote_requested_date)

        # Manager creates sale.order (minimal direct linking, skipping wizard)
        so = self._make_sale_order_linked(repair)
        self.assertEqual(so.state, 'draft')
        self.assertEqual(repair.quote_state, 'pending')

        # Manager sends mail
        so.state = 'sent'
        self.assertEqual(repair.quote_state, 'sent')
        self.assertTrue(repair.quote_sent_date)

        # Client accepts on portal
        so.with_user(self.manager_user_1).state = 'sale'
        self.assertEqual(repair.quote_state, 'approved')

    def test_full_refusal_path_triggers_activity(self):
        repair = self._make_repair()
        repair.action_atelier_request_quote()
        so = self._make_sale_order_linked(repair)
        so.state = 'sent'
        so.state = 'cancel'
        self.assertEqual(repair.quote_state, 'refused')
        repair.invalidate_recordset(['has_open_refusal_activity'])
        self.assertTrue(repair.has_open_refusal_activity)

    def test_cron_stops_on_state_transition(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('sent')
        repair.quote_sent_date = fields.Datetime.now() - timedelta(days=10)
        repair.last_reminder_sent_at = fields.Datetime.now() - timedelta(days=4)

        # Before: would create escalation
        self.Repair._cron_process_pending_quotes()
        repair.invalidate_recordset(['has_open_escalation'])
        self.assertTrue(repair.has_open_escalation)

        # Move to approved — escalation activities should be closed
        repair._apply_quote_state_transition('approved')
        repair.invalidate_recordset(['has_open_escalation'])
        self.assertFalse(repair.has_open_escalation)

        # CRON should now find nothing to do
        self.Repair._cron_process_pending_quotes()
