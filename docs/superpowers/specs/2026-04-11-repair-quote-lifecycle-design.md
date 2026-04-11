# Repair Quote Lifecycle Automation — Design Spec

**Sub-project 2 of 3** in the repair workflow overhaul.
**Date:** 2026-04-11
**Target module:** `repair_custom` (extension, no new addon)
**Depends on:** `repair_custom`, `sale_management`, `mail`, `portal`

## Context and scope

The repair shop needs to turn the current ad-hoc quote workflow into a semi-automated lifecycle. Today:

- Technicians click "Demander un devis" which creates one personal activity per manager
- Managers check their individual activity feeds, open the repair, run the pricing wizard to generate a `sale.order`
- Mail is sent manually via Odoo's native sale.order portal flow
- Client acceptance/refusal happens on the native portal but there is no reminder, no escalation, no shared queue, no state consistency between `sale.order.state` and `repair.quote_state`
- Refused quotes are handled entirely by hand

This sub-project introduces:

1. A refactored quote state machine with a new `sent` state
2. Automatic synchronisation between `sale.order.state` and `repair.quote_state` via a single write-level override
3. A shared "Devis à préparer" queue replacing the per-manager activity pattern
4. A CRON reminder cascade (reminder mail → escalation activity → "Contacté" reset) cloning the sub-project 1 pattern
5. Minimal refusal handling that defers pickup orchestration to sub-project 3

### Related sub-projects

1. **Appointment system** (sub-project 1, already delivered) — pickup scheduling infrastructure
2. **Quote lifecycle automation** (this spec) — tech request → manager review → client mail → CRON cascade → approved/refused sync
3. **Completion → pickup → invoice → SAR** (future) — ties the quote document into the completion mail, invoices at pickup, starts SAR warranty

### Out of scope for this sub-project

- **Mail template for "ready for pickup"** — belongs to sub-project 3
- **Automatic creation of a `repair.pickup.appointment` on quote refusal** — sub-project 2 opens a "statuer" activity; sub-project 3 will add a "Créer RDV retrait" button later
- **Completion mail with validated quote attached** — sub-project 3
- **Invoice generation and delivered/SAR transitions at pickup** — sub-project 3
- **SMS reminders** — Odoo Community, no IAP, mail only (same constraint as sub-project 1)
- **Partial acceptance of a batch quote** — one batch = one `sale.order` = one client decision. Managers handle partial scenarios manually by removing devices from the batch before creating the quote
- **Multiple reminder mails** — one reminder per cycle, then human escalation. Consistent with sub-project 1
- **Contre-proposition / quote amendment workflow** — if a quote is refused and a new one is tried, that is a cancellation + new request, not an amendment
- **Dedicated manual validation/refusal buttons on the repair form** — we reuse native `sale.order` `action_confirm` / `action_cancel` via the sync mechanism
- **Electronic signature** — already free via the native Odoo portal; nothing to build

## Decisions captured from brainstorming

| # | Decision | Why |
|---|---|---|
| 1 | Flexibility type **B**: automated by default, bypass available at each step | User runs an artisan business. Every automation must be overridable via standard Odoo actions, without adding custom bypass UI when a native one already exists |
| 2 | One spec for sub-project 2 only, sub-project 3 later | Scoped delivery worked for sub-project 1. Less risk, cleaner control points. Some boundary decisions may be revisited when sub-project 3 starts — acceptable cost |
| 3 | **A**: reuse Odoo native `sale.order` portal for client accept/refuse, customise the mail template later | Battle-tested code, free electronic signature, smaller surface to maintain. User will customise the mail body separately |
| 4 | **C**: shared "Devis à préparer" view + suppression of per-manager activities + menu badge counter | Eliminates the "I did my activity but my colleague still sees theirs" confusion. Single source of truth. Badge gives individual awareness without duplicate notifications |
| 5 | **A**: minimal refusal handling — set `quote_state=refused`, open a "statuer" activity for the manager, defer pickup appointment creation to sub-project 3 | Keeps sub-project 2 focused. Avoids duplicating the "ready for pickup" mail template that belongs to sub-project 3. Forward-compatible: a button will be added to the activity when sub-project 3 arrives |
| 6 | CRON pattern cloned from sub-project 1: hourly run, single reminder, then escalation activity, "Contacté" resets the escalation clock, no second reminder mail | Consistency across the two CRONs. Same mental model for the team |
| 7 | Default delays: 5 days reminder / 3 days escalation (longer than sub-project 1's 3/3 for RDVs because quote decisions take more thought) | Configurable via `res.config.settings`, both are `ir.config_parameter` |
| 8 | **quote_state refactor**: drop unused `draft`, add `sent`. New flow: `none → pending → sent → approved / refused` | `draft` was never used in the current code. `sent` is required to give the CRON a clear domain to observe |
| 9 | **No new validation/refusal buttons on `repair.order`**. Reuse native `sale.order.action_confirm` and `action_cancel` via a `sale.order.write()` override that propagates state to `repair.quote_state` | User suggestion. Cleaner architecture: single source of truth (`sale.order.state`), native Odoo UI, zero duplicate buttons |
| 10 | **A**: tech notification on quote approval via chatter mention only | Works with or without `user_id` on `hr.employee`. No risk of dead feature for techs without Odoo accounts. Minimalist, non-intrusive |
| 11 | One batch = one `sale.order` = one quote decision. The pricing wizard already creates this structure | Consistent with sub-project 1's "one RDV per batch" discipline. Partial acceptance is a manual edge case |
| 12 | `pricing_wizard` stays as-is. It already generates both quotes and invoices, loops through batch devices, builds sections per device, forces service VAT, uses the existing `sale_order_template_repair_quote` | No alternative to build. Wizard is the only entry point for quote creation |

## Architecture

### Guiding principle

> Each automation is active by default but each step has an obvious manual bypass. The manager must always be able to short-circuit the flow via standard Odoo actions, without custom-built bypass UI.

### Module layout (addition to existing `repair_custom`)

```
repair_custom/
├── models/
│   ├── repair_order.py                    # MODIFIED: state machine, transitions, CRON, bypasses
│   ├── sale_order.py                      # MODIFIED: write() override for sync
│   └── res_config_settings.py             # MODIFIED/NEW: quote delays
├── data/
│   ├── mail_templates.xml                 # MODIFIED: add reminder template
│   ├── mail_activity_type_data.xml        # MODIFIED: add escalate + refused types
│   └── ir_cron.xml                        # MODIFIED/NEW: quote CRON
├── views/
│   ├── repair_order_views.xml             # MODIFIED: add "Contacté" button, queue tree/search view
│   ├── menu_views.xml                     # MODIFIED: add "Devis" menu group
│   └── res_config_settings_views.xml      # MODIFIED: add quote delays section
├── tests/
│   └── test_quote_lifecycle.py            # NEW
└── migrations/
    └── 17.0.X.Y/
        └── post-migration.py              # NEW: draft → none cleanup
```

No new module. All changes live inside `repair_custom`.

## State machine

### New `repair.quote_state`

```
                    (quote_required=False OR default)
                               │
                               ▼
                           ┌────────┐
                           │  none  │
                           └────┬───┘
                                │
                                │ action_atelier_request_quote()
                                │ (tech button)
                                ▼
                           ┌────────┐
                           │pending │◄─────┐
                           └────┬───┘      │
                                │          │
                                │          │ sale.order re-draft
                                │          │ (manager cancels then re-opens)
                                │          │
             pricing_wizard     │          │
             creates sale.order │          │
             (stays pending)    │          │
                                │          │
                                │ manager  │
                                │ clicks   │
                                │ "Envoyer │
                                │ par mail"│
                                ▼          │
                           ┌────────┐      │
                      ┌────│  sent  │──────┘
                      │    └────┬───┘
                      │         │
      action_confirm  │         │  action_cancel
      (manager button │         │  (manager button
       OR client      │         │   OR client portal)
       portal)        │         │
                      ▼         ▼
                  ┌────────┐ ┌────────┐
                  │approved│ │refused │
                  └────────┘ └────────┘
                  (terminal, reversible only via sale.order re-draft)
```

| Old state | New state | Notes |
|---|---|---|
| `none` | `none` | Unchanged |
| `draft` (unused) | **removed** | Migration script sets existing records to `none` (expected zero rows) |
| `pending` | `pending` | **Semantics clarified**: "tech requested, manager must prepare OR sale.order created but not yet sent" |
| — | `sent` (**new**) | Mail sent, waiting client, inside CRON scope |
| `approved` | `approved` | Unchanged |
| `refused` | `refused` | Unchanged |

## Data model

### New fields on `repair.order`

| Field | Type | Notes |
|---|---|---|
| `quote_state` | Selection | **Modified**: drop `draft`, add `sent` |
| `quote_requested_date` | Datetime | Set by `action_atelier_request_quote`. Used for sorting in the manager queue and audit |
| `quote_sent_date` | Datetime | Set by `_apply_quote_state_transition` on entry to `sent`. Starting point for the CRON reminder delay calculation |
| `last_reminder_sent_at` | Datetime | Timestamp when the CRON sent the reminder mail. `False` until the reminder is sent |
| `contacted` | Boolean | Flag consumed by the CRON after the manager clicks "Contacté". Auto-reset to `False` when the next escalation is created |
| `contacted_at` | Datetime | Timestamp of the "Contacté" click, for the next `escalation_delay` calculation |
| `has_open_escalation` | Boolean (computed, stored) | `True` if an open `mail_act_repair_quote_escalate` activity exists on the repair. Stored for fast filtering in the queue view and menu badge. Depends on `activity_ids.state`, `activity_ids.activity_type_id` |
| `has_open_refusal_activity` | Boolean (computed, stored) | `True` if an open `mail_act_repair_quote_refused` activity exists. Same dependency pattern |

**On the cost of stored computes**: Odoo recalculates on every write to `activity_ids`. For an artisan shop with a few dozen active repairs, this is negligible. Alternative (non-stored + search override) would be lighter on write but heavier on read for the badge counter and queue. The stored choice is the correct trade-off here.

### No new fields on `repair.batch`

The batch model doesn't need to evolve for sub-project 2. The pricing wizard continues to create one `sale.order` per batch, and all repairs in the batch share the same `sale_order_id` (already handled by the existing wizard code).

### No new fields on `sale.order`

`sale.order.state` is the source of truth. `sale_order.repair_order_ids` (already present via `repair_custom/models/repair_extensions.py:202`) is used to locate linked repairs during sync.

### New config parameters

Stored via `ir.config_parameter`, UI in `res.config.settings`:

| Key | Default | Description |
|---|---|---|
| `repair_custom.quote_reminder_delay_days` | `5` | Days after `quote_state='sent'` before the reminder mail is sent |
| `repair_custom.quote_escalation_delay_days` | `3` | Days after the reminder (or after "Contacté") before the escalation activity is created |

### New mail activity types

Added to `repair_custom/data/mail_activity_type_data.xml`:

| XML ID | Default summary | Icon |
|---|---|---|
| `mail_act_repair_quote_escalate` | *Client à contacter — devis non validé* | `fa-phone` |
| `mail_act_repair_quote_refused` | *Devis refusé — statuer sur la réparation* | `fa-times-circle` |

Both with `res_model_id = repair.order`, `delay_count = 0`, `delay_unit = 'days'`, `delay_from = 'current_date'`.

### New mail template

Added to `repair_custom/data/mail_templates.xml`:

```xml
<record id="mail_template_repair_quote_reminder" model="mail.template">
    <field name="name">Rappel devis de réparation</field>
    <field name="model_id" ref="model_repair_order"/>
    <field name="subject">Rappel : votre devis de réparation {{ object.name }}</field>
    <field name="email_from">{{ object.company_id.email_formatted or user.email_formatted }}</field>
    <field name="email_to">{{ object.partner_id.email }}</field>
    <field name="body_html" type="html">
        <!-- Minimalist body, user will customise later -->
    </field>
</record>
```

### New CRON

Added to `repair_custom/data/ir_cron.xml`:

```xml
<record id="ir_cron_repair_quote_process" model="ir.cron">
    <field name="name">Repair: Process pending quotes</field>
    <field name="model_id" ref="model_repair_order"/>
    <field name="state">code</field>
    <field name="code">model._cron_process_pending_quotes()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">hours</field>
    <field name="numbercall">-1</field>
    <field name="active">True</field>
</record>
```

## Synchronisation `sale.order.state` → `repair.quote_state`

**Principle:** `sale.order.state` is the canonical source of truth. `repair.quote_state` is computed/synchronised in reaction.

**Mechanism:** override `sale.order.write()` to detect state transitions and propagate them to linked repair orders through a single entry point.

### Mapping table

| `sale.order.state` | → | `repair.quote_state` | Chatter note on the repair |
|---|---|---|---|
| `draft` (creation or re-draft) | → | `pending` | *« Devis remis en préparation »* (only on re-entry, not initial create) |
| `sent` (manager clicks "Envoyer par mail") | → | `sent` | *« 📧 Devis envoyé au client »* |
| `sale` **via action_confirm** (manager, backoffice) | → | `approved` | *« ✅ Devis validé manuellement par {user} »* |
| `sale` **via portal** (client accept) | → | `approved` | *« ✅ Devis accepté par le client via le portail »* |
| `cancel` **via action_cancel** (manager, backoffice) | → | `refused` | *« ❌ Devis annulé manuellement par {user} »* + refusal activity |
| `cancel` **via portal** (client refuse) | → | `refused` | *« ❌ Devis refusé par le client via le portail »* + refusal activity |

**Distinguishing "manual by manager" vs "client via portal"**: the override checks `self.env.user.share` (True for portal users). The distinction is used only for the chatter note wording; the state transition itself is identical.

### Sync implementation sketch

```python
# repair_custom/models/sale_order.py
class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def write(self, vals):
        state_before = {rec.id: rec.state for rec in self}
        res = super().write(vals)
        if 'state' in vals:
            self._sync_repair_quote_state(state_before)
        return res

    def _sync_repair_quote_state(self, state_before):
        mapping = {
            'draft':  'pending',
            'sent':   'sent',
            'sale':   'approved',
            'cancel': 'refused',
        }
        for order in self:
            if not order.repair_order_ids:
                continue
            old = state_before.get(order.id)
            new = order.state
            if old == new:
                continue
            target = mapping.get(new)
            if not target:
                continue
            for repair in order.repair_order_ids:
                repair._apply_quote_state_transition(target, from_sale_order=True)
```

### Single entry point: `_apply_quote_state_transition`

All transitions of `quote_state` (from the sync override, from `action_atelier_request_quote`, from the CRON, from any future path) funnel through one method:

```python
def _apply_quote_state_transition(self, new_state, from_sale_order=False):
    """Single entry point for quote_state transitions. Fans out side effects."""
    for rec in self:
        old = rec.quote_state
        if old == new_state:
            continue
        rec.quote_state = new_state
        is_portal_action = self.env.user.share if from_sale_order else False

        if new_state == 'sent':
            rec.quote_sent_date = fields.Datetime.now()
            rec.message_post(body=_("📧 Devis envoyé au client."))

        elif new_state == 'approved':
            if is_portal_action:
                rec.message_post(body=_("✅ Devis accepté par le client via le portail."))
            else:
                rec.message_post(body=_(
                    "✅ Devis validé manuellement par %s."
                ) % self.env.user.name)
            rec._notify_tech_quote_approved()
            rec._close_escalation_activities()

        elif new_state == 'refused':
            if is_portal_action:
                rec.message_post(body=_("❌ Devis refusé par le client via le portail."))
            else:
                rec.message_post(body=_(
                    "❌ Devis annulé manuellement par %s."
                ) % self.env.user.name)
            rec._create_refusal_activity()
            rec._close_escalation_activities()

        elif new_state == 'pending' and old in ('sent', 'approved', 'refused'):
            rec.message_post(body=_("↩ Devis remis en préparation."))
```

### Why this architecture

1. **Zero desync risk** — one entry point for transitions means there is literally no other way to write `quote_state` (except the CRON which also goes through this method)
2. **Side effects centralised** — tech mention, refusal activity, escalation closure all happen in one place
3. **Native Odoo portal compatibility** — the Odoo-provided portal controller writes `sale.order.state`; our `write()` override catches it automatically; zero custom controller code
4. **Tests target one method** — `_apply_quote_state_transition` is the single surface to cover for state-change behaviour

### Simplification of `action_atelier_request_quote`

The existing `action_atelier_request_quote` currently loops over every manager in `group_repair_manager` and creates one activity each (repair_order.py:901-908). In the new mechanic, this loop is **removed**. The method becomes:

```python
def action_atelier_request_quote(self):
    self.ensure_one()
    self._assign_technician_if_needed()
    if not self.internal_notes:
        raise UserError(_("Veuillez remplir l'estimation technique avant de demander un devis."))
    self._apply_quote_state_transition('pending')
    self.quote_requested_date = fields.Datetime.now()
    self.message_post(body=_(
        "🔖 Devis demandé par %s."
    ) % (self.technician_employee_id.name or self.env.user.name))
    return True
```

The shared "Devis à préparer" queue + menu badge replace the per-manager activity pattern.

## Reminder CRON

**Frequency:** hourly (`ir.cron`, `interval_number=1, interval_type='hours'`). Day-granularity logic, but hourly decouples from wall-clock edge cases. Consistent with sub-project 1.

### Algorithm

```python
def _cron_process_pending_quotes(self):
    today = fields.Datetime.now()
    Params = self.env['ir.config_parameter'].sudo()
    reminder_delay = int(Params.get_param('repair_custom.quote_reminder_delay_days', 5))
    escalation_delay = int(Params.get_param('repair_custom.quote_escalation_delay_days', 3))

    sent_repairs = self.search([
        ('quote_state', '=', 'sent'),
        ('quote_sent_date', '!=', False),
    ])

    for repair in sent_repairs:
        # Phase 1: the single reminder mail
        if (not repair.last_reminder_sent_at
                and not repair.contacted
                and today >= repair.quote_sent_date + timedelta(days=reminder_delay)):
            repair._send_quote_reminder_mail()
            repair.last_reminder_sent_at = today
            continue

        # Phase 2: escalation activity
        if repair.has_open_escalation:
            continue  # wait for "Contacté"

        if repair.contacted:
            if today >= repair.contacted_at + timedelta(days=escalation_delay):
                repair._create_quote_escalation_activity()
                repair.contacted = False  # consume
        elif repair.last_reminder_sent_at:
            if today >= repair.last_reminder_sent_at + timedelta(days=escalation_delay):
                repair._create_quote_escalation_activity()
```

### Stop conditions

The CRON domain filters on `quote_state == 'sent'`. Any transition (via the sync, via a manual button, via the CRON itself) automatically drops the repair from processing. `_apply_quote_state_transition` also calls `_close_escalation_activities()` on terminal transitions to keep the activity feed clean.

### Escalation activity semantics

Same pattern as sub-project 1: `mail.activity.user_id` is a single-user M2O; "manager group" is implemented by creating **one activity per manager**. When any one of them clicks "Contacté", `action_quote_contacted` marks **all** sibling activities as done (joined by `res_id + activity_type_id`) and sets `contacted=True`, `contacted_at=now()`. No mail is sent on "Contacté".

### Escalation activity metadata

- `activity_type_id` — `mail_act_repair_quote_escalate` (custom type for filter clarity and label control)
- `summary` — *« Client à contacter — devis non validé »*
- `note` — link to the repair + link to the `sale.order` + client phone + date the quote was originally sent
- `date_deadline` — `today`

### Refusal activity metadata

- `activity_type_id` — `mail_act_repair_quote_refused`
- `summary` — *« Devis refusé — statuer sur la réparation »*
- `note` — *« Le devis pour {device_name} a été refusé. Action requise. »* + link to the repair
- One activity per manager in `group_repair_manager`
- When sub-project 3 arrives, a "Créer RDV retrait" button will be added to the activity form that calls `batch.action_create_pickup_appointment`. Until then, the manager handles the refusal manually (phone call, direct cancel, etc.)

## Manager queue and UI

### Menu placement

New top-level group "Devis" under the repair module, positioned after the existing main repair entries, before "Rendez-vous" (sub-project 1) if installed.

```
Réparations
├── Réparations (existing)
├── Batches (existing)
├── Devis                              ← NEW
│   └── Devis à préparer (default action)
├── Rendez-vous (sub-project 1)
└── Configuration
```

Visible to `group_repair_manager` and `group_repair_admin`. **Hidden from `group_repair_technician`** — technicians don't need to see the manager queue.

### Action `action_repair_quote_manager_queue`

| Attribute | Value |
|---|---|
| `name` | `Devis à préparer` |
| `res_model` | `repair.order` |
| `view_mode` | `tree,form` |
| `domain` | `[('quote_state', 'in', ['pending', 'sent']), ('state', 'not in', ['cancel', 'done', 'irreparable'])]` |
| `context` | `{'search_default_to_prepare': 1}` (pre-activates the "À préparer" filter) |

### Tree view `view_repair_quote_queue_tree`

New standalone tree view on `repair.order`, reserved for this action (not inherited).

**Columns:**
- `name` (clickable → opens the repair form)
- `partner_id`
- `device_id_name`
- `technician_employee_id`
- `internal_notes` (truncated, tooltip on hover)
- `quote_requested_date`
- `quote_state` (badge, colour per state)
- `has_open_escalation` (invisible, used for decoration)
- `last_reminder_sent_at` (invisible, used for decoration)

**Row decorations:**
- `decoration-warning` — `quote_state == 'sent' AND last_reminder_sent_at` (reminder already sent)
- `decoration-danger` — `has_open_escalation`

### Search view

**Predefined filters:**
- *À préparer* — `quote_state=pending AND sale_order_id=False`
- *À envoyer* — `quote_state=pending AND sale_order_id!=False`
- *En attente client* — `quote_state=sent AND has_open_escalation=False`
- *À relancer* — `quote_state=sent AND last_reminder_sent_at!=False AND has_open_escalation=False`
- *À contacter* — `has_open_escalation=True`
- *Devis refusés (à statuer)* — `quote_state=refused AND has_open_refusal_activity=True` (expands the default domain)

**Group-by:** `technician_employee_id`, `partner_id`, `quote_state`

### Menu badge counter

The "Devis" menu shows a badge with the number of repairs requiring manager attention.

**Formula:**
```python
count = repair.order where
    (quote_state == 'pending' AND sale_order_id == False)      # à préparer
    OR (quote_state == 'pending' AND sale_order_id != False)   # à envoyer
    OR (has_open_escalation == True)                           # à contacter
    OR (quote_state == 'refused' AND has_open_refusal_activity)# à statuer
```

**Explicitly excluded:** repairs just in `sent` state without open escalation. The badge must always reflect actions waiting **for the manager**, not just volume in flight.

**Implementation:** Odoo 17 `needaction_count` mechanism on a synthetic menu entry, or a `web_tour`-style override. **Fallback plan** if the badge proves too complex: display the count inside the menu label directly, refreshed on page navigation. Prefer a slightly degraded feature over a performance bug.

### Form view changes

**New header button on `repair.order` form:**

```xml
<xpath expr="//header" position="inside">
    <button name="action_quote_contacted"
            type="object"
            string="Contacté"
            class="oe_highlight"
            attrs="{'invisible': [('has_open_escalation', '=', False)]}"/>
</xpath>
```

Visible only when `has_open_escalation == True`. Single click, no dialog.

**No other new buttons.** The existing `action_atelier_request_quote`, `action_create_quotation_wizard`, and the Sale Order smart button remain. Manual validate/refuse happens via native `sale.order` buttons.

### Settings form

New section in `res.config.settings` view under "Réparation":

```
──────── Réparation ────────
[...existing settings...]

  Devis
  ─────
  Délai avant relance devis    [ 5] jours
    (envoi du rappel mail si le client n'a pas répondu)

  Délai avant escalade devis   [ 3] jours
    (création de l'activité "client à contacter")
```

Placed under the existing sub-project 1 "Rendez-vous" section for visual consistency.

## New methods on `repair.order`

| Method | Visibility | Role |
|---|---|---|
| `_apply_quote_state_transition(new_state, from_sale_order=False)` | Private | **Single entry point** for `quote_state` transitions. Called from the `sale.order.write` override, the CRON, and `action_atelier_request_quote` |
| `_notify_tech_quote_approved()` | Private | Posts a chatter message with `@technician_employee_id.user_id` mention (pattern A). Fallback to plain chatter note if no `user_id` |
| `_close_escalation_activities()` | Private | Marks all open `mail_act_repair_quote_escalate` activities on the repair as done |
| `_create_refusal_activity()` | Private | Creates N refusal activities (one per manager in `group_repair_manager`) |
| `_create_quote_escalation_activity()` | Private | Creates N escalation activities (one per manager) |
| `_send_quote_reminder_mail()` | Private | Sends `mail_template_repair_quote_reminder` |
| `action_quote_contacted()` | Public | "Contacté" button handler. Closes sibling escalation activities, sets `contacted=True`, `contacted_at=now()`, posts chatter note |
| `_cron_process_pending_quotes()` | Model-level | CRON worker, called hourly |

## New methods on `sale.order`

| Method | Visibility | Role |
|---|---|---|
| `write(vals)` | Override | Captures `state` before and after, calls `_sync_repair_quote_state` on transitions |
| `_sync_repair_quote_state(state_before)` | Private | Applies mapping table, calls `_apply_quote_state_transition` on each linked repair |

## Security

No new groups. Access control via existing groups:

- `group_repair_technician` — can call `action_atelier_request_quote` (unchanged)
- `group_repair_manager` — full access to the queue view, sees the menu, can click "Contacté", can run the pricing wizard, can use native `sale.order` buttons
- `group_repair_admin` — additionally can edit settings

**Portal routes:** no new routes. The native Odoo sale portal already handles client-facing accept/refuse. Our sync override runs `sudo()` implicitly through the portal user context.

## Testing strategy

New test file: `repair_custom/tests/test_quote_lifecycle.py`

### `TestQuoteStateMachine`

| Test | Coverage |
|---|---|
| `test_tech_request_transitions_to_pending` | `action_atelier_request_quote` sets `pending`, `quote_requested_date`, posts chatter, creates **no** manager activities (regression test against legacy behaviour) |
| `test_tech_request_requires_internal_notes` | `UserError` if `internal_notes` is empty |
| `test_pending_to_sent_via_sale_order_mail` | Pricing wizard creates sale.order → `quote_state` stays `pending`. "Send by email" (sale.order → `sent`) → write override → `quote_state=sent`, `quote_sent_date` set |
| `test_sent_to_approved_via_client_portal` | Portal context (`user.share=True`) + `sale.order.action_confirm` → `quote_state=approved`, chatter note mentions "via le portail" |
| `test_sent_to_approved_via_manager_manual` | Manager user + `sale.order.action_confirm` → `quote_state=approved`, chatter note mentions "manuellement par {user}" |
| `test_sent_to_refused_via_portal` | Portal context + `sale.order.action_cancel` → `quote_state=refused`, refusal activity created for each manager |
| `test_sent_to_refused_via_manager` | Manager user + `sale.order.action_cancel` → same result, different chatter wording |
| `test_refused_back_to_pending_via_sale_order_redraft` | Re-draft of a cancelled sale.order → `quote_state` returns to `pending` |
| `test_tech_notification_on_approved` | `_notify_tech_quote_approved` posts message with `@technician_employee_id.user_id` mention |
| `test_tech_notification_without_user_id` | Tech without `user_id` on `hr.employee` → no crash, fallback chatter note |

### `TestQuoteCronReminder`

Uses `freezegun` or manual `fields.Datetime.now` patching.

| Test | Coverage |
|---|---|
| `test_reminder_not_sent_before_delay` | `quote_sent_date + (reminder_delay - 1 day)` → CRON runs, nothing happens |
| `test_reminder_sent_at_delay` | `quote_sent_date + reminder_delay` → CRON sends mail, sets `last_reminder_sent_at` |
| `test_reminder_sent_only_once` | After reminder sent, next CRON run does **not** send a second mail |
| `test_escalation_activity_created_after_delay` | `last_reminder_sent_at + escalation_delay` → CRON creates N escalation activities |
| `test_contacted_resets_escalation_clock` | Click `action_quote_contacted` → activities marked done, `contacted=True`, `contacted_at` set. Next CRON does **not** recreate until `contacted_at + escalation_delay` |
| `test_contacted_re_escalation` | After `contacted_at + escalation_delay`, CRON recreates the escalation, `contacted` reset to `False` |
| `test_cron_stops_on_state_transition` | Repair in `sent` leaves CRON scope on state change. Open escalation activities are closed automatically |
| `test_cron_ignores_repairs_without_quote_sent_date` | Edge case: `quote_state='sent'` but `quote_sent_date` null → no crash |

### `TestQuoteViewsAndFilters`

| Test | Coverage |
|---|---|
| `test_queue_domain_shows_pending_and_sent` | Action domain returns the correct repairs |
| `test_queue_domain_excludes_terminal_states` | `done`/`cancel`/`irreparable` repairs absent |
| `test_filter_to_prepare` | Filter `pending AND sale_order_id=False` |
| `test_filter_to_send` | Filter `pending AND sale_order_id!=False` |
| `test_filter_escalations` | Filter `has_open_escalation=True` |
| `test_has_open_escalation_compute` | Adding/closing an escalation activity updates the stored field |
| `test_has_open_refusal_activity_compute` | Same for the refusal activity |

### `TestSaleOrderSync`

| Test | Coverage |
|---|---|
| `test_write_hooks_sync_states` | Each `sale.order.state` transition triggers the correct `quote_state` |
| `test_write_hook_no_repair_link_no_op` | Sale order not linked to a repair → override doesn't crash |
| `test_write_hook_batch_sale_order_syncs_all_repairs` | Sale order linked to N repairs from a batch → all synchronised in one pass |
| `test_write_hook_idempotent_on_same_state` | Re-writing the same state doesn't re-trigger side effects |

### Common fixtures

Either in the existing `tests/common.py` (if present) or a new `TestQuoteLifecycleCommon`:
- 1 partner client
- 1 `hr.employee` with `user_id` (tech A)
- 1 `hr.employee` without `user_id` (tech B, for fallback test)
- 2 manager users in `group_repair_manager`
- 1 `repair.order` with `quote_required=True`, `internal_notes` filled
- 1 product service with `default_code='SERV'` for the pricing wizard
- Config: `quote_reminder_delay_days=5`, `quote_escalation_delay_days=3`

### Manual QA checklist

1. Install/upgrade the module. Verify the `draft → none` migration.
2. Create a repair, fill `internal_notes`, check `quote_required`, click "Demander un devis". Verify: `quote_state=pending`, menu badge +1, **no** activities on the repair.
3. Open the "Devis à préparer" view. Verify the repair appears under the "À préparer" filter.
4. Open the repair, click "Créer le devis", complete the pricing wizard. Verify: sale.order created, repair still in "À préparer".
5. Open the sale.order via smart button, click "Envoyer par mail". Verify: `quote_state=sent`, `quote_sent_date` set, badge still +1, repair moved to "En attente client" filter.
6. Rewind `quote_sent_date` by 6 days in the database. Manually trigger the CRON. Verify: reminder mail sent, `last_reminder_sent_at` set.
7. Rewind `last_reminder_sent_at` by 4 days. Re-trigger CRON. Verify: N escalation activities created, menu badge updated.
8. Click "Contacté" on the repair. Verify: all activities marked done, chatter note, `contacted=True`.
9. Rewind `contacted_at` by 4 days. Re-trigger CRON. Verify: new escalation created, `contacted=False`.
10. On the sale.order, click "Confirm". Verify: `quote_state=approved`, tech mention in chatter, escalation closed, repair leaves the menu badge.
11. Create another repair, send quote, click "Cancel" on the sale.order in the backoffice. Verify: `quote_state=refused`, refusal activity created for each manager.
12. Simulate a portal refusal: from an Odoo shell, `sale_order.with_user(portal_user).action_cancel()`. Verify the chatter distinguishes "via le portail".
13. Verify menu badge is 0 when all repairs are in terminal states.

## Migration plan

### Script

`repair_custom/migrations/17.0.X.Y/post-migration.py`:

```python
def migrate(cr, version):
    cr.execute("""
        UPDATE repair_order
        SET quote_state = 'none'
        WHERE quote_state = 'draft'
    """)
```

Expected to affect zero rows — `draft` is never written by the current code. Safety net.

### Legacy activities

Repairs already in `quote_state='pending'` at deploy time may have `mail_act_repair_quote_validate` activities (the existing type) created by the old code. **These are left in place.** The new behaviour (no activities) applies only to new quote requests post-deploy. Rationale: removing activities a manager is working on would break trust in the system.

### Coexistence with sub-project 1

Sub-project 1 (appointment) already added a similar CRON. Both run independently on different models. **Zero interaction.** The two sets of settings (appointment delays vs quote delays) live in the same config section but are completely independent.

### Git branch

New branch `feature/repair-quote-lifecycle` created from `main` **after** `feature/repair-appointment` is merged. All sub-project 2 work happens on the new branch.

## Boundary with sub-project 3

**What sub-project 2 delivers that sub-project 3 will consume:**
- `sale_order_id` on the repair, in a known `approved` state when the repair is done → sub-project 3 will attach the validated quote document to the completion mail
- `_apply_quote_state_transition` as the single entry point → sub-project 3 can call it if needed for edge cases
- Refusal activity `mail_act_repair_quote_refused` → sub-project 3 will add a "Créer RDV retrait" button to the activity form
- `repair.batch` relationship unchanged → sub-project 3's pickup appointment hook already works

**What sub-project 2 defers to sub-project 3:**
- Creating a `repair.pickup.appointment` on refusal (sub-project 3 owns the "ready for pickup" mail template)
- Completion mail with validated quote attached
- Invoice generation, delivered transition, SAR warranty start

These deferrals are known-good: the two sub-projects touch different parts of the workflow and have clean interface boundaries.

## Open questions deferred to sub-project 3

- Exact content of the "ready for pickup" mail (does it include the validated quote document as attachment, or a link to the portal sale.order, or both?)
- Should the "Créer RDV retrait" button on the refusal activity create a pickup appointment immediately or open a wizard first?
- How does the re-creation of a quote after refusal interact with a pending pickup appointment (if sub-project 3 creates one)?
