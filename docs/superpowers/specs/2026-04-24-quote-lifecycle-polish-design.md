# Quote Lifecycle Polish — Design

**Date:** 2026-04-24
**Origin:** QA findings against Feature 3 (Quote Lifecycle Automation) from
`docs/superpowers/QA-checklist-2026-04-24.md` §5.
**Scope:** Three targeted fixes to the quote reminder/escalation flow. No
architectural change — this is Track B of a three-track QA follow-up
(Tracks A and C are documented in `QA-2026-04-24-followup-tracks.md`).

## Problems to solve

1. **Queue ordering is not user-controllable.** Managers working the "Devis à
   préparer" queue want oldest-first triage; current tree has no quick way to
   sort by request age.
2. **Reminder mail is broken for non-portal-account clients.** The reminder
   template is bound to `repair.order` and the "view quote" link calls
   `sale_order_id.get_portal_url()` without an access token. Clients without a
   portal account cannot open the link.
3. **Escalation is not firing** on staging. Symptom: quote reminders go out,
   but the post-reminder escalation activity never appears. Reporter observed
   that escalation "seems computed off `quote_requested_date` instead of
   `quote_sent_date`", which the code contradicts — so an empirical debug is
   needed.

## Non-goals

- Not moving the reminder mail off the repair's audit trail. Staff watch the
  repair; a chatter line there is how they know a reminder went out.
- Not redesigning the CRON's phase structure. The pattern already matches the
  appointment CRON (`_cron_process_pending_appointments`); any divergence
  between the two is a bug to find, not a design to change.
- Not changing `default_order` on the tree. Users pick the sort themselves.

## Design

### 5a — User-selectable sort on the quote queue

Odoo search views do not support "order" as a selectable filter. The canonical
pattern is clickable column headers.

**Change (`repair_custom/views/repair_quote_queue_views.xml`):**
- Line 18: `quote_requested_date` — drop `optional="show"` so the column is
  always visible (not hideable via the column picker).
- Line 19: `quote_sent_date` — switch `optional="hide"` → `optional="show"` so
  managers working the "En attente client" / "À relancer" tabs can sort by
  send date with one click.
- No change to `default_order` (model default remains in effect).

Users who want oldest-first click the `quote_requested_date` header; Odoo
persists the sort per-user via favorites.

### 5b — Reminder mail: mirror `action_quotation_send` plumbing

The goal is the same mechanics as `sale.order.action_quotation_send` (portal
token on the link, mail on the quote's thread) but with our reminder
template. A short chatter line stays on the repair for staff audit.

**Template change (`repair_custom/data/mail_templates.xml`):**
- `mail_template_repair_quote_reminder`:
  - `model_id`: `model_repair_order` → `model_sale_order`. `object` in Jinja
    is now the quote.
  - Body rewrite:
    - Greeting: `object.partner_id.name` (unchanged — SO has partner_id).
    - Device label: `object.repair_id.device_id_name or 'appareil'` (Feature 7
      added `repair_id` back-reference on sale.order for per-repair quotes).
    - Repair ref: `object.repair_id.name`.
    - Quote-view link: use an access-token-signed URL. Python side calls
      `_portal_ensure_token()` before rendering (see method below), so the
      quote has an `access_token`. The template renders:
      ```
      <a t-att-href="object.get_portal_url()">Voir le devis</a>
      ```
      `sale.order.get_portal_url()` on a record with an access token returns
      the signed URL (same link `action_quotation_send` produces).

**Method change (`repair_custom/models/repair_order.py` around line 1396):**

Replace `_send_quote_reminder_mail`:

```python
def _send_quote_reminder_mail(self):
    template = self.env.ref(
        'repair_custom.mail_template_repair_quote_reminder',
        raise_if_not_found=False,
    )
    if not template:
        return
    for rec in self:
        if not rec.sale_order_id:
            continue
        # Mirror action_quotation_send: ensure portal access_token exists
        # so the link in the mail is usable without a portal login.
        rec.sale_order_id._portal_ensure_token()
        template.send_mail(
            rec.sale_order_id.id,
            force_send=False,
            email_layout_xmlid='mail.mail_notification_light',
        )
        # Keep the audit trail on the repair thread so staff watching the
        # repair see that the reminder went out.
        rec.message_post(body=_(
            "📧 Rappel de devis envoyé au client (devis %s)."
        ) % rec.sale_order_id.name)
```

Key properties:
- Email is posted on the **sale.order** thread (native quote mail history,
  matches `action_quotation_send`'s side effects).
- Audit line posted on the **repair** thread.
- `email_layout_xmlid='mail.mail_notification_light'` matches Odoo's default
  quote-send layout (no heavy-handed corporate signature block).
- Skips repairs without a `sale_order_id` (defensive — should not occur since
  the CRON domain requires `quote_state='sent'`, which implies an SO exists).

### 5c — Escalation debug

No code redesign — the CRON pattern already matches
`_cron_process_pending_appointments`. Plan includes:

1. **Instrumentation** (temporary, removable after debug): at the top of
   `_cron_process_pending_quotes` (`repair_order.py:1408`), log
   `len(sent_repairs)` and per-record `id, quote_sent_date,
   last_reminder_sent_at, contacted, contacted_at, has_open_escalation`.
2. **Staging repro**: back-date `quote_sent_date` past `reminder_delay +
   escalation_delay` on a known-good sent quote; run the CRON manually;
   review the log.
3. **Hypothesis checklist** to verify while reading the log:
   - `has_open_escalation` compute stuck at True (stale dependency on
     `activity_ids.state`).
   - XMLID `repair_custom.mail_act_repair_quote_escalate` missing from data
     (silent early-return at `repair_order.py:1451`).
   - `group_repair_manager.users` empty on the tenant.
   - Record excluded by the CRON's domain filter
     (`quote_state='sent'` + `quote_sent_date != False`).
4. **Outcome branch:**
   - If it's a code bug, open a follow-up fix in the same branch.
   - If it's config/data, document in the PR body and close the QA item.
5. Instrumentation is removed before merge.

## Data model changes

None.

## Migration / backwards compatibility

None. Template rewrite is in a `noupdate="1"` data file — the migration script
for this bump needs to **force-update** the template record once (via
`noupdate="0"` in migrations or a direct SQL/ORM update in a
`pre-migrate.py`). Simpler path: bump `__manifest__.py` version and write a
small `post-migrate.py` that:

```python
def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.ref('repair_custom.mail_template_repair_quote_reminder').write({
        'model_id': env.ref('sale.model_sale_order').id,
        'body_html': NEW_BODY_HTML,
        'subject': NEW_SUBJECT,
    })
```

(Plan will finalize the migration mechanism.)

## Testing

**Unit / integration:**
- Existing `repair_custom/tests/test_quote_lifecycle.py` covers the CRON phase
  transitions (reminder → escalation). After the method rewrite, update the
  reminder-send test to assert:
  - `sale_order_id.message_ids` grows by 1 mail.
  - Repair chatter gets the "Rappel de devis envoyé" line.
  - `sale_order_id.access_token` is populated post-send.

**Manual on staging:**
- Send a reminder to a test partner **without a portal account**. Click the
  link from a fresh browser (no session). Expect to land on the quote portal
  page without a login prompt.
- Verify the mail appears on the SO chatter (with the portal layout).
- Verify the repair chatter shows the audit line.

**Debug (5c):** per plan, re-run CRON on staging with back-dated record and
log review. Must produce either a fix commit or a written config/data
diagnosis before merge.

## Rollout

- Branch: `fix/quote-lifecycle-polish-qa`.
- Single PR covering 5a + 5b + 5c (5c may include a bug fix if debug surfaces
  one).
- Manifest bump: `repair_custom` to next patch version with a `post-migrate`
  entry for the template rewrite.
- Redeploy CRON (`ir_cron_repair_quote_process`) refreshes on module update;
  no separate action needed.
