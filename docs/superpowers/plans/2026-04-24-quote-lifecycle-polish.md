# Quote Lifecycle Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three QA findings in the quote reminder/escalation flow: make the queue user-sortable, rework the reminder mail to use access-token-signed portal links (mirroring `sale.order.action_quotation_send`), and empirically debug why escalation isn't firing on staging.

**Architecture:** Three independent workstreams on a single branch (`fix/quote-lifecycle-polish-qa`, already created from `main`). Workstream 1 is a pure view tweak. Workstream 2 rebinds a mail template from `repair.order` to `sale.order`, rewrites `_send_quote_reminder_mail` to send via the SO while keeping audit chatter on the repair, and ships a `post-migrate.py` that force-updates the template on existing installs. Workstream 3 adds temporary debug logging to `_cron_process_pending_quotes`, runs a staging repro against a back-dated record, and branches on the outcome (code fix vs. config/data diagnosis documented in PR body).

**Tech Stack:** Odoo 17, Python 3.10+, PostgreSQL. `portal.mixin._portal_ensure_token()` for signed access URLs. `mail.template.send_mail()` for rendered sends. pytest-style `odoo.tests.common.TransactionCase`.

**Spec:** `docs/superpowers/specs/2026-04-24-quote-lifecycle-polish-design.md`

**Branch:** `fix/quote-lifecycle-polish-qa` (already exists, spec committed on it)

---

## Workstream 1 — Queue sorting (5a)

### Task 1: Make sort-relevant columns always visible

**Files:**
- Modify: `repair_custom/views/repair_quote_queue_views.xml:18-19`

**Context:** Odoo search views do not support "order" as a selectable filter. The canonical pattern is clickable column headers. We surface both date columns as always-visible (not hideable via the column picker) so one click on the header sorts the queue. Users who want oldest-first click `quote_requested_date` ascending; Odoo persists the sort per-user via favorites.

- [ ] **Step 1: Edit the tree view**

Remove `optional="show"` from `quote_requested_date` and change `optional="hide"` → `optional="show"` on `quote_sent_date`:

```xml
<!-- Before -->
<field name="quote_requested_date" widget="datetime" options="{'show_time':false}" optional="show"/>
<field name="quote_sent_date" widget="date" optional="hide"/>

<!-- After -->
<field name="quote_requested_date" widget="datetime" options="{'show_time':false}"/>
<field name="quote_sent_date" widget="date" optional="show"/>
```

(Dropping `optional` entirely on `quote_requested_date` makes it always-rendered. Keeping `optional="show"` on `quote_sent_date` means the column renders by default but users can hide it from the picker if they want — matches the "users choose" spirit while still making it trivially accessible.)

- [ ] **Step 2: Restart Odoo and confirm the view loads**

Run:
```bash
workon odoo_dev
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```
Expected: zero tracebacks, "Module repair_custom: loaded" or equivalent.

- [ ] **Step 3: Manual smoke test**

Open Odoo UI → Repairs → Devis → Devis à préparer. Expected:
- `quote_requested_date` column is visible without touching the optional-column picker.
- `quote_sent_date` column is visible by default.
- Clicking the `quote_requested_date` header once sorts ascending (oldest-first); clicking again sorts descending.

- [ ] **Step 4: Commit**

```bash
git add repair_custom/views/repair_quote_queue_views.xml
git commit -m "repair_custom: surface quote date columns for one-click sort (QA 5a)"
```

---

## Workstream 2 — Reminder mail rework (5b)

### Task 2: Bump module version and scaffold migration directory

**Files:**
- Modify: `repair_custom/__manifest__.py:5`
- Create: `repair_custom/migrations/17.0.1.10.0/post-migrate.py`

**Context:** Odoo only runs a migration script when the module version increases. The template rewrite in Task 4 must be applied to existing installs — `noupdate="1"` data records are not overwritten on `-u` by default, so we bump the version and write a `post-migrate.py` that forces the template's `model_id`, `subject`, and `body_html` to the new values.

- [ ] **Step 1: Bump the manifest version**

Edit `repair_custom/__manifest__.py` line 5:

```python
# Before
'version': '17.0.1.9.0',
# After
'version': '17.0.1.10.0',
```

- [ ] **Step 2: Create the migration directory with an empty placeholder script**

```bash
mkdir -p repair_custom/migrations/17.0.1.10.0
```

Create `repair_custom/migrations/17.0.1.10.0/post-migrate.py` with a stub that does nothing yet (real logic comes in Task 5, after the template XML is finalized):

```python
# -*- coding: utf-8 -*-
"""Post-migration for 17.0.1.10.0.

Force-rewrite `mail_template_repair_quote_reminder` because the template
is in a `noupdate="1"` data file and a plain `-u` would not refresh the
`model_id` / `subject` / `body_html` on existing installs.

The actual rewrite is applied by re-reading the loaded template record
after the XML has been reloaded and pushing its current XML-declared
values onto any existing row.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Populated in Task 5 once the new template XML is authoritative.
    _logger.info("post-migrate 17.0.1.10.0: placeholder — template rewrite pending")
```

- [ ] **Step 3: Restart to confirm manifest parses**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```
Expected: loads cleanly; log line `post-migrate 17.0.1.10.0: placeholder — template rewrite pending`.

- [ ] **Step 4: Commit**

```bash
git add repair_custom/__manifest__.py repair_custom/migrations/17.0.1.10.0/post-migrate.py
git commit -m "repair_custom: bump to 17.0.1.10.0 with post-migrate scaffold"
```

---

### Task 3: Update the failing test first (TDD)

**Files:**
- Modify: `repair_custom/tests/test_quote_lifecycle.py:240-248`

**Context:** The existing test `test_send_quote_reminder_mail_posts_message` asserts that calling `_send_quote_reminder_mail()` grows `repair.message_ids`. That assertion still holds (the new method posts an audit chatter line on the repair), but we want a stronger test that also asserts the mail lands on the SO thread and the SO has an `access_token`. We write the stricter test first so it fails with current code, then implement Task 4 to make it pass.

- [ ] **Step 1: Replace the reminder-send test class with the stricter version**

Locate the existing test (around line 239-248):

```python
class TestSendQuoteReminderMail(RepairQuoteCase):
    """Test the reminder mail helper in isolation."""

    def test_send_quote_reminder_mail_posts_message(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('sent')
        before = len(repair.message_ids)
        repair._send_quote_reminder_mail()
        self.assertGreater(len(repair.message_ids), before,
                           "Sending the reminder should post a tracked message on the repair")
```

Replace with:

```python
class TestSendQuoteReminderMail(RepairQuoteCase):
    """Test the reminder mail helper in isolation."""

    def _setup_sent_quote(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('pending')
        sale_order = self._make_sale_order_linked(repair)
        sale_order.state = 'sent'  # triggers the sale.order.write override → quote_state='sent'
        return repair, sale_order

    def test_send_quote_reminder_posts_audit_line_on_repair(self):
        repair, _so = self._setup_sent_quote()
        before = len(repair.message_ids)
        repair._send_quote_reminder_mail()
        self.assertGreater(len(repair.message_ids), before,
                           "Reminder must post an audit chatter line on the repair")

    def test_send_quote_reminder_sends_mail_from_sale_order(self):
        repair, sale_order = self._setup_sent_quote()
        before_so = len(sale_order.message_ids)
        repair._send_quote_reminder_mail()
        self.assertGreater(len(sale_order.message_ids), before_so,
                           "Reminder mail must thread on the sale.order, not the repair")

    def test_send_quote_reminder_ensures_access_token(self):
        repair, sale_order = self._setup_sent_quote()
        # Pre-condition: freshly created SOs typically have no access_token
        sale_order.access_token = False
        repair._send_quote_reminder_mail()
        self.assertTrue(sale_order.access_token,
                        "Reminder must ensure the quote has a portal access_token "
                        "so the link works without a portal login")

    def test_send_quote_reminder_without_sale_order_is_noop(self):
        repair = self._make_repair()
        repair._apply_quote_state_transition('pending')
        # No sale_order_id linked
        # Should not crash
        repair._send_quote_reminder_mail()
```

- [ ] **Step 2: Run the test suite to confirm the new tests fail**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf --test-tags=/repair_custom:TestSendQuoteReminderMail --stop-after-init
```
Expected:
- `test_send_quote_reminder_posts_audit_line_on_repair` — **FAIL** (old impl posts a mail on repair but not a distinct chatter line)
- `test_send_quote_reminder_sends_mail_from_sale_order` — **FAIL** (old impl sends from repair)
- `test_send_quote_reminder_ensures_access_token` — **FAIL** (old impl does not call `_portal_ensure_token`)
- `test_send_quote_reminder_without_sale_order_is_noop` — may PASS on old impl (sends a mail from the repair even without SO), but will be correct under new impl.

- [ ] **Step 3: Commit the failing tests**

```bash
git add repair_custom/tests/test_quote_lifecycle.py
git commit -m "repair_custom: TDD — stricter tests for _send_quote_reminder_mail (QA 5b)"
```

---

### Task 4: Rewrite `_send_quote_reminder_mail`

**Files:**
- Modify: `repair_custom/models/repair_order.py:1396-1405`

**Context:** Current implementation calls `template.send_mail(rec.id, ...)` with `rec` being the repair. We rewrite to send from the linked `sale.order` (after ensuring its `access_token`), then post a short chatter note on the repair for the audit trail. The layout `mail.mail_notification_light` matches Odoo's default quote-send appearance.

- [ ] **Step 1: Replace `_send_quote_reminder_mail`**

Old code (lines 1396-1405):

```python
def _send_quote_reminder_mail(self):
    """Send the reminder mail template to the client."""
    template = self.env.ref(
        'repair_custom.mail_template_repair_quote_reminder',
        raise_if_not_found=False,
    )
    if not template:
        return
    for rec in self:
        template.send_mail(rec.id, force_send=False)
```

New code:

```python
def _send_quote_reminder_mail(self):
    """Send the quote reminder mail, mirroring sale.order.action_quotation_send
    plumbing: mail is rendered against (and threaded on) the sale.order with a
    signed portal access_token on the view-quote link. A short audit line is
    posted on the repair thread so staff tracking the repair still see it.
    """
    template = self.env.ref(
        'repair_custom.mail_template_repair_quote_reminder',
        raise_if_not_found=False,
    )
    if not template:
        return
    for rec in self:
        if not rec.sale_order_id:
            continue
        rec.sale_order_id._portal_ensure_token()
        template.send_mail(
            rec.sale_order_id.id,
            force_send=False,
            email_layout_xmlid='mail.mail_notification_light',
        )
        rec.message_post(body=_(
            "📧 Rappel de devis envoyé au client (devis %s)."
        ) % rec.sale_order_id.name)
```

- [ ] **Step 2: Verify `_` (translation helper) is already imported in this file**

Check top of `repair_custom/models/repair_order.py` for an existing `from odoo import ..., _` import. The file already uses `_("...")` in neighboring methods (e.g. line 1379, 1393), so the import is present — no change needed. Bash sanity check:

```bash
grep -n "^from odoo import" repair_custom/models/repair_order.py
```
Expected: a line including `_` in the imported names.

- [ ] **Step 3: Run the tests again; expect all 4 to pass**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf --test-tags=/repair_custom:TestSendQuoteReminderMail -u repair_custom --stop-after-init
```
Expected: all 4 tests PASS.

- [ ] **Step 4: Run the full quote lifecycle test class to confirm no regressions**

```bash
./odoo-bin -c ../odoo.conf --test-tags=/repair_custom:TestCronReminderPhase,/repair_custom:TestCronEscalationPhase -u repair_custom --stop-after-init
```
Expected: all tests PASS. The CRON tests don't assert on mail threading (only on `last_reminder_sent_at` and `has_open_escalation`), so they stay green.

- [ ] **Step 5: Commit**

```bash
git add repair_custom/models/repair_order.py
git commit -m "repair_custom: reminder mail sends from sale.order with access token (QA 5b)"
```

---

### Task 5: Rebind the mail template to `sale.order`

**Files:**
- Modify: `repair_custom/data/mail_templates.xml`

**Context:** With `_send_quote_reminder_mail` now calling `template.send_mail(sale_order_id, ...)`, the template must render with `object = sale.order`. We rebind `model_id`, rewrite the body to pull device/repair context via the `repair_id` back-reference (added by Feature 7), and use `object.get_portal_url()` for the link — `sale.order` inherits `portal.mixin` and `get_portal_url()` returns a URL that includes the `access_token` query string when one exists on the record (which `_send_quote_reminder_mail` guarantees via `_portal_ensure_token`).

- [ ] **Step 1: Rewrite the template**

Replace the entire contents of `repair_custom/data/mail_templates.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <data noupdate="1">

        <record id="mail_template_repair_quote_reminder" model="mail.template">
            <field name="name">Rappel devis de réparation</field>
            <field name="model_id" ref="sale.model_sale_order"/>
            <field name="subject">Rappel : votre devis de réparation {{ object.repair_id.name or object.name }}</field>
            <field name="email_from">{{ object.company_id.email_formatted or user.email_formatted }}</field>
            <field name="email_to">{{ object.partner_id.email }}</field>
            <field name="lang">{{ object.partner_id.lang }}</field>
            <field name="body_html" type="html">
                <div style="margin: 0px; padding: 0px;">
                    <p>Bonjour <t t-out="object.partner_id.name or ''"/>,</p>
                    <p>
                        Nous vous avons adressé il y a quelques jours un devis pour la réparation de votre
                        <t t-out="(object.repair_id.device_id_name if object.repair_id else False) or 'appareil'"/>
                        (<t t-out="(object.repair_id.name if object.repair_id else object.name) or ''"/>).
                    </p>
                    <p>
                        N'hésitez pas à nous revenir avec votre décision afin que nous puissions planifier
                        les travaux.
                    </p>
                    <p>
                        Vous pouvez consulter et valider le devis directement en ligne :
                        <a t-att-href="object.get_portal_url()">Voir le devis</a>
                    </p>
                    <p>Cordialement,</p>
                    <p><t t-out="user.company_id.name or ''"/></p>
                </div>
            </field>
        </record>

    </data>
</odoo>
```

Key differences from the old template:
- `model_id` is now `sale.model_sale_order`.
- Subject and body pull repair context via `object.repair_id` (the back-reference Feature 7 added on sale.order).
- Link uses `object.get_portal_url()` (the SO's portal URL helper); the preceding `_portal_ensure_token()` call in the Python method ensures an access_token exists, which `get_portal_url()` will embed.
- Dropped the `t-if="object.sale_order_id"` guard around the link — the link is always renderable because `object` IS the sale.order now.

- [ ] **Step 2: Populate the post-migrate script**

Replace the stub in `repair_custom/migrations/17.0.1.10.0/post-migrate.py` with the full implementation:

```python
# -*- coding: utf-8 -*-
"""Post-migration for 17.0.1.10.0.

Force-rewrite `mail_template_repair_quote_reminder` because the template
lives in a `noupdate="1"` data file. Plain `-u` would not refresh the
model_id / subject / body_html on existing installs, so we push the
module's new XML values over the existing row.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


NEW_SUBJECT = "Rappel : votre devis de réparation {{ object.repair_id.name or object.name }}"

NEW_BODY = """
                <div style="margin: 0px; padding: 0px;">
                    <p>Bonjour <t t-out="object.partner_id.name or ''"/>,</p>
                    <p>
                        Nous vous avons adressé il y a quelques jours un devis pour la réparation de votre
                        <t t-out="(object.repair_id.device_id_name if object.repair_id else False) or 'appareil'"/>
                        (<t t-out="(object.repair_id.name if object.repair_id else object.name) or ''"/>).
                    </p>
                    <p>
                        N'hésitez pas à nous revenir avec votre décision afin que nous puissions planifier
                        les travaux.
                    </p>
                    <p>
                        Vous pouvez consulter et valider le devis directement en ligne :
                        <a t-att-href="object.get_portal_url()">Voir le devis</a>
                    </p>
                    <p>Cordialement,</p>
                    <p><t t-out="user.company_id.name or ''"/></p>
                </div>
"""


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref(
        'repair_custom.mail_template_repair_quote_reminder',
        raise_if_not_found=False,
    )
    if not template:
        _logger.warning(
            "post-migrate 17.0.1.10.0: template mail_template_repair_quote_reminder not found — skipping"
        )
        return
    sale_order_model = env.ref('sale.model_sale_order')
    template.write({
        'model_id': sale_order_model.id,
        'subject': NEW_SUBJECT,
        'body_html': NEW_BODY,
    })
    _logger.info(
        "post-migrate 17.0.1.10.0: rewrote mail_template_repair_quote_reminder to sale.order"
    )
```

- [ ] **Step 3: Restart Odoo with update; confirm template rewrite fires**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init 2>&1 | grep -E "post-migrate 17\.0\.1\.10\.0|ERROR|Traceback" | head -20
```
Expected output includes:
```
post-migrate 17.0.1.10.0: rewrote mail_template_repair_quote_reminder to sale.order
```
No ERROR / Traceback lines.

- [ ] **Step 4: Verify template state in DB**

```bash
./odoo-bin shell -c ../odoo.conf --no-http <<'PY'
tpl = env.ref('repair_custom.mail_template_repair_quote_reminder')
print('model:', tpl.model)
print('subject:', tpl.subject)
assert tpl.model == 'sale.order', f"expected sale.order, got {tpl.model}"
print('OK')
PY
```
Expected: `model: sale.order`, `OK`.

- [ ] **Step 5: Rerun full test class to confirm template change composes with method change**

```bash
./odoo-bin -c ../odoo.conf --test-tags=/repair_custom:TestSendQuoteReminderMail -u repair_custom --stop-after-init
```
Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add repair_custom/data/mail_templates.xml repair_custom/migrations/17.0.1.10.0/post-migrate.py
git commit -m "repair_custom: rebind quote reminder template to sale.order (QA 5b)"
```

---

### Task 6: Manual staging smoke test for the reminder link

**Files:** (no file changes — verification task)

**Context:** Automated tests assert the `access_token` is set and the mail threads on the SO, but only a real click from a logged-out browser confirms the URL actually opens the portal quote page without a login redirect.

- [ ] **Step 1: Prepare a test quote on staging**

In Odoo UI as a manager:
1. Create a repair for a test partner whose email you control.
2. Confirm, request quote, build + send a quote via the pricing wizard + sale.order send flow.
3. Back-date `quote_sent_date` on the repair to 6 days ago (via developer mode field editor):
   ```
   Settings → Technical → Database Structure: not needed; just edit repair.quote_sent_date via dev-mode field editor.
   ```
4. Trigger the CRON manually: Settings → Technical → Scheduled Actions → `ir_cron_repair_quote_process` → Run Manually.

- [ ] **Step 2: Check mail went out, threaded on SO**

Open the sale.order record → chatter pane should show an outgoing mail with the Odoo "quote mail" layout. Open the repair → chatter shows the audit line `📧 Rappel de devis envoyé au client (devis SO-xxxxx).`.

- [ ] **Step 3: Click the link from a logged-out browser**

Open the reminder mail in your test inbox, copy the "Voir le devis" link. Paste it into a fresh private/incognito window. Expected:
- Lands directly on the portal quote page.
- No login prompt, no access-denied page.
- Quote lines and partner info render correctly.

If the link redirects to a login page, the `access_token` is not embedded — check `sale_order.access_token` in the backend and re-run.

- [ ] **Step 4: (No commit — verification only)**

If any failure: open a `fixup!` commit referencing the most recent commit from Task 5 and iterate.

---

## Workstream 3 — Escalation debug (5c)

### Task 7: Add temporary instrumentation to `_cron_process_pending_quotes`

**Files:**
- Modify: `repair_custom/models/repair_order.py:1407-1439`

**Context:** The reporter says escalation activities are not being created on staging. The code pattern already matches `_cron_process_pending_appointments`, so this is likely a data/config issue rather than a logic bug — but we need evidence. Add per-record logging so the next CRON run surfaces why each candidate is (or isn't) escalated. This instrumentation is **temporary** and will be removed before merge (Task 10).

- [ ] **Step 1: Add logging lines inside the CRON**

Modify `_cron_process_pending_quotes` (starts at line 1407) to add debug logs without changing behavior:

```python
@api.model
def _cron_process_pending_quotes(self):
    """Hourly CRON: reminder + escalation cascade for sent quotes."""
    today = fields.Datetime.now()
    Params = self.env['ir.config_parameter'].sudo()
    reminder_delay = int(Params.get_param('repair_custom.quote_reminder_delay_days', 5))
    escalation_delay = int(Params.get_param('repair_custom.quote_escalation_delay_days', 3))

    sent_repairs = self.search([
        ('quote_state', '=', 'sent'),
        ('quote_sent_date', '!=', False),
    ])

    # [QA-5c debug] Remove before merge once diagnosis is filed.
    _logger.info(
        "[QA-5c] _cron_process_pending_quotes: %d candidate(s); "
        "reminder_delay=%d escalation_delay=%d today=%s",
        len(sent_repairs), reminder_delay, escalation_delay, today,
    )
    for r in sent_repairs:
        _logger.info(
            "[QA-5c] candidate repair=%s quote_sent_date=%s last_reminder_sent_at=%s "
            "contacted=%s contacted_at=%s has_open_escalation=%s",
            r.name, r.quote_sent_date, r.last_reminder_sent_at,
            r.contacted, r.contacted_at, r.has_open_escalation,
        )

    for repair in sent_repairs:
        # Phase 1: the single reminder mail
        if (not repair.last_reminder_sent_at
                and not repair.contacted
                and today >= repair.quote_sent_date + timedelta(days=reminder_delay)):
            _logger.info("[QA-5c] %s: phase-1 reminder fires", repair.name)
            repair._send_quote_reminder_mail()
            repair.last_reminder_sent_at = today
            continue

        # Phase 2: escalation activity
        if repair.has_open_escalation:
            _logger.info("[QA-5c] %s: phase-2 skipped — has_open_escalation=True", repair.name)
            continue

        if repair.contacted:
            if repair.contacted_at and today >= repair.contacted_at + timedelta(days=escalation_delay):
                _logger.info("[QA-5c] %s: phase-2 escalating (after contacted_at)", repair.name)
                repair._create_quote_escalation_activity()
                repair.contacted = False
            else:
                _logger.info("[QA-5c] %s: phase-2 deferred (contacted, waiting escalation_delay)", repair.name)
        elif repair.last_reminder_sent_at:
            if today >= repair.last_reminder_sent_at + timedelta(days=escalation_delay):
                _logger.info("[QA-5c] %s: phase-2 escalating (after last_reminder_sent_at)", repair.name)
                repair._create_quote_escalation_activity()
            else:
                _logger.info("[QA-5c] %s: phase-2 deferred (reminder sent, waiting escalation_delay)", repair.name)
        else:
            _logger.info("[QA-5c] %s: phase-2 skipped — no reminder sent yet", repair.name)
```

- [ ] **Step 2: Verify `_logger` is already imported at the top of the file**

```bash
grep -n "^_logger\s*=\s*logging" repair_custom/models/repair_order.py
```
Expected: one match near the top of the file. If absent, add `import logging` and `_logger = logging.getLogger(__name__)` near other imports.

- [ ] **Step 3: Run tests to confirm instrumentation did not break behavior**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf --test-tags=/repair_custom:TestCronReminderPhase,/repair_custom:TestCronEscalationPhase -u repair_custom --stop-after-init
```
Expected: all CRON tests PASS.

- [ ] **Step 4: Commit**

```bash
git add repair_custom/models/repair_order.py
git commit -m "repair_custom: [QA-5c] temporary CRON instrumentation for escalation debug"
```

---

### Task 8: Run the staging repro

**Files:** (no file changes — diagnostic task)

**Context:** On staging (clone of production), set up a known-good repro that *should* escalate, run the CRON, read the logs, form a diagnosis.

- [ ] **Step 1: Deploy the branch to staging**

On the staging host:
```bash
cd /path/to/custom_addons
git fetch origin fix/quote-lifecycle-polish-qa
git checkout fix/quote-lifecycle-polish-qa
```

Restart Odoo on staging with `-u repair_custom`.

- [ ] **Step 2: Create a repro record**

As a manager in the staging UI:
1. Pick an existing sent quote (or create one end-to-end).
2. Back-date `quote_sent_date` to today minus `(reminder_delay + escalation_delay + 1)` days. With defaults that is `5 + 3 + 1 = 9` days ago.
3. Set `last_reminder_sent_at` to today minus `(escalation_delay + 1)` days = 4 days ago.
4. Confirm `contacted=False`, `contacted_at=False`, no open escalation activities on the record.

(The shortcut: in dev-mode field editor, write both timestamps directly. The CRON should then detect phase 2 is due and create escalation activities.)

- [ ] **Step 3: Run the CRON manually and tail logs**

```bash
# Option A: via UI — Settings → Technical → Scheduled Actions → ir_cron_repair_quote_process → Run Manually
# Option B: via shell
./odoo-bin shell -c /path/to/odoo.conf --no-http <<'PY'
env['repair.order']._cron_process_pending_quotes()
env.cr.commit()
PY
```

Tail the log file and grep for the markers:
```bash
grep "\[QA-5c\]" /path/to/odoo.log | tail -50
```

- [ ] **Step 4: Capture the findings**

Read the log output. Expected one of these outcomes:

| Log pattern | Diagnosis |
|---|---|
| `candidate repair=X ... has_open_escalation=True` on a record that should escalate | Bug: `has_open_escalation` compute stale or depends on the wrong field. Investigate the compute and its `@api.depends`. |
| `phase-2 escalating` line but no activity appears on the record | Bug: `_create_quote_escalation_activity` early-returns — likely `mail_act_repair_quote_escalate` XMLID missing or `group_repair_manager.users` empty. |
| `phase-2 skipped — no reminder sent yet` on a record whose `last_reminder_sent_at` is populated | Cache/ORM issue — invalidate before the loop. |
| No `candidate repair=` lines at all | Domain filter excludes the record. Either `quote_state != 'sent'` or `quote_sent_date is False`. Check the record in the UI. |
| `phase-2 escalating` and activity IS created | No bug — staging was not actually in the escalation window during the reporter's observation. Close as "config/timing". |

Save the log excerpt + diagnosis to a file for the PR body:
```bash
grep "\[QA-5c\]" /path/to/odoo.log > /tmp/qa-5c-diagnosis.txt
```

- [ ] **Step 5: (No commit yet — proceeds to Task 9)**

---

### Task 9: Fix or document (branch on Task 8 outcome)

**Files:** depends on diagnosis — see sub-paths below.

**Context:** Task 8 yields one of two outcomes. Follow the matching sub-path.

#### Sub-path 9A: Diagnosis is a code bug

- [ ] **Step 1: Write a failing regression test reproducing the bug**

Add a test to `repair_custom/tests/test_quote_lifecycle.py` under `TestCronEscalationPhase` that sets up the exact state captured in the log and asserts an escalation activity **is** created. The precise test body depends on the diagnosis — e.g. if the compute is stale, force the state that reveals the staleness and assert `repair.has_open_escalation == False` before the CRON runs.

Example shape (adapt to the actual bug):

```python
def test_escalation_fires_after_closed_activity_cleared(self):
    """Regression: after a closed escalation activity is feedback'd,
    has_open_escalation must recompute to False so the next CRON window
    can fire a fresh escalation."""
    # Set up state matching the staging log
    self.Repair._cron_process_pending_quotes()  # creates first escalation
    activity = self.repair.activity_ids.filtered(
        lambda a: a.activity_type_id.xml_id == 'repair_custom.mail_act_repair_quote_escalate'
    )
    activity.action_feedback(feedback="manual close")
    self.repair.invalidate_recordset(['has_open_escalation'])
    self.assertFalse(self.repair.has_open_escalation)
    # fast-forward past another escalation window
    self.repair.last_reminder_sent_at = fields.Datetime.now() - timedelta(days=10)
    self.Repair._cron_process_pending_quotes()
    # Assert a NEW escalation activity was created
    open_activities = self.repair.activity_ids.filtered(
        lambda a: a.activity_type_id.xml_id == 'repair_custom.mail_act_repair_quote_escalate'
        and a.state != 'done'
    )
    self.assertTrue(open_activities, "Should have fired a second escalation after the first was closed")
```

- [ ] **Step 2: Confirm the test fails**

```bash
./odoo-bin -c ../odoo.conf --test-tags=/repair_custom:TestCronEscalationPhase -u repair_custom --stop-after-init
```
Expected: new test FAILs.

- [ ] **Step 3: Apply the minimal fix**

Depends on the bug. Examples:
- Stale `has_open_escalation`: add missing field to its `@api.depends`, or swap the compute to search `activity_ids.state != 'done'`.
- Missing XMLID: add the data record to `repair_custom/data/mail_activity_data.xml`.

Write the minimal code change.

- [ ] **Step 4: Run the test; confirm it passes**

```bash
./odoo-bin -c ../odoo.conf --test-tags=/repair_custom:TestCronEscalationPhase -u repair_custom --stop-after-init
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add repair_custom/tests/test_quote_lifecycle.py repair_custom/models/repair_order.py
git commit -m "repair_custom: fix escalation cascade <specific bug> (QA 5c)"
```

#### Sub-path 9B: Diagnosis is config/data

- [ ] **Step 1: Write a short markdown note**

Create `docs/superpowers/QA-5c-diagnosis.md`:

```markdown
# QA 5c — Escalation non-firing: Diagnosis

**Date:** <fill>
**Outcome:** Config/data issue, no code change required.

## Staging repro

<describe the record set up in Task 8>

## Log excerpt

<paste the relevant `[QA-5c]` lines from `/tmp/qa-5c-diagnosis.txt`>

## Root cause

<one of: `mail_act_repair_quote_escalate` missing; `group_repair_manager.users`
empty; clock skew; etc.>

## Remediation on production

<steps the ops owner should take, e.g. "ensure at least one user is in
group_repair_manager", "re-install module to restore the activity type">

## Why this wasn't a code bug

The CRON pattern matches `_cron_process_pending_appointments` exactly. Logs
confirm Phase 1 and Phase 2 branches execute correctly; the escalation path
was gated by <X> which is a deployment/data concern, not a logic concern.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/QA-5c-diagnosis.md
git commit -m "docs: document QA 5c escalation diagnosis (config/data, no code fix)"
```

---

### Task 10: Remove the temporary instrumentation

**Files:**
- Modify: `repair_custom/models/repair_order.py` (revert Task 7)

**Context:** Debug logging was added in Task 7 for diagnostic purposes. With the diagnosis now filed (Task 9), the `[QA-5c]` log lines are noise. Remove them, keep the method body as it was before Task 7 — plus any fix applied under Sub-path 9A.

- [ ] **Step 1: Strip the `[QA-5c]` logging lines from `_cron_process_pending_quotes`**

The method should end up identical to its pre-Task-7 form (if 9B was taken) or identical to pre-Task-7 + the 9A fix (if 9A was taken). Use the diff to verify only `_logger.info("[QA-5c] ...")` lines are removed, no logic changes:

```bash
git diff HEAD -- repair_custom/models/repair_order.py
```
Expected: only deletions of `_logger.info("[QA-5c]...` lines.

- [ ] **Step 2: Run the full test class to confirm removal didn't break anything**

```bash
./odoo-bin -c ../odoo.conf --test-tags=/repair_custom:TestCronReminderPhase,/repair_custom:TestCronEscalationPhase,/repair_custom:TestSendQuoteReminderMail -u repair_custom --stop-after-init
```
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add repair_custom/models/repair_order.py
git commit -m "repair_custom: remove temporary QA-5c CRON instrumentation"
```

---

## Workstream 4 — Final verification and PR

### Task 11: Full-suite regression and PR creation

**Files:** (no file changes — verification + PR)

- [ ] **Step 1: Run the full repair_custom test suite**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf --test-tags=/repair_custom -u repair_custom --stop-after-init 2>&1 | tail -30
```
Expected: zero failures. If any unrelated failures appear, investigate — do not suppress.

- [ ] **Step 2: Review the branch diff**

```bash
git log --oneline main..fix/quote-lifecycle-polish-qa
git diff main..fix/quote-lifecycle-polish-qa --stat
```
Expected commit sequence (order may vary slightly around Task 9 sub-path):
1. `spec: quote lifecycle polish (QA follow-up Track B)`
2. `repair_custom: surface quote date columns for one-click sort (QA 5a)`
3. `repair_custom: bump to 17.0.1.10.0 with post-migrate scaffold`
4. `repair_custom: TDD — stricter tests for _send_quote_reminder_mail (QA 5b)`
5. `repair_custom: reminder mail sends from sale.order with access token (QA 5b)`
6. `repair_custom: rebind quote reminder template to sale.order (QA 5b)`
7. `repair_custom: [QA-5c] temporary CRON instrumentation for escalation debug`
8. Task 9 commit (either fix or diagnosis doc)
9. `repair_custom: remove temporary QA-5c CRON instrumentation`

- [ ] **Step 3: Push the branch**

```bash
git push -u origin fix/quote-lifecycle-polish-qa
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "Quote lifecycle polish (QA follow-up Track B)" --body "$(cat <<'EOF'
## Summary
- 5a: Quote queue date columns always-visible for one-click user-selected sort
- 5b: Reminder mail rebinds to `sale.order`, uses `_portal_ensure_token()` so the "view quote" link works for clients without a portal account; audit chatter line stays on the repair
- 5c: <outcome from Task 9 — either "Fixed <specific bug> in escalation cascade" OR "Diagnosed as config/data; see docs/superpowers/QA-5c-diagnosis.md">

## Test plan
- [ ] `repair_custom` full test suite green
- [ ] Manual on staging: send reminder, open the link from a logged-out browser, land on portal quote without login prompt
- [ ] Manual on staging: back-date quote_sent_date, run CRON, confirm escalation activity appears
- [ ] Spec: `docs/superpowers/specs/2026-04-24-quote-lifecycle-polish-design.md`
- [ ] Tracks A and C still outstanding; see `docs/superpowers/QA-2026-04-24-followup-tracks.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review checklist (run before handing off)

- [x] **Spec coverage:** 5a → Task 1; 5b → Tasks 2–6; 5c → Tasks 7–10; version bump + migration → Tasks 2, 5.
- [x] **No placeholders:** every code step has the exact code; every command has expected output.
- [x] **Type/name consistency:** `_send_quote_reminder_mail` signature preserved (no args, iterates `self`); `object.repair_id` used in both XML template and post-migrate body verbatim; XMLID `sale.model_sale_order` used in both template XML and post-migrate.
- [x] **Branching logic:** Task 9 has two explicit sub-paths with full steps for each.
- [x] **Commit hygiene:** every non-verification task ends in a commit; commit messages tag the QA item.
