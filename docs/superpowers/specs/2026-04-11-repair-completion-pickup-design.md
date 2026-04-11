# Repair Completion → Pickup → Invoice → SAR — Design Spec

**Sub-project 3 of 3** in the repair workflow overhaul.
**Date:** 2026-04-11
**Target module:** `repair_custom` (extension, no new addon) + one small edit in `repair_appointment`
**Depends on:** `repair_custom`, `repair_appointment` (sub-project 1), `sale_management`, `account`, `mail`, `stock`

## Context and scope

Sub-project 1 shipped the pickup appointment infrastructure (model, portal, CRON, calendar). Sub-project 2 shipped the quote lifecycle automation. Both stopped at well-defined integration hooks that sub-project 3 now fills:

- Sub-project 1's `repair.batch.action_create_pickup_appointment(notify=True)` creates an appointment and sends a mail template whose body is currently a stub.
- Sub-project 2 leaves the accepted `sale.order` as the source of truth for the quote document.
- Neither sub-project decides **when** the client notification fires, **what** it contains, or **how** the physical pickup at the counter is processed.

Sub-project 3 ties these ends together:

1. When a technician completes a repair and this brings the whole batch to terminal state, offer a post-transition dialog "Notifier le client maintenant ?".
2. Provide a fallback "Notifier client" button on repairs whose batch is ready but has not yet been notified.
3. Expand the `mail_template_pickup_ready` body with a repair summary, conditional quote PDF attachment, and the portal link.
4. At the counter: a "Traiter le retrait" button routes to the linked `sale.order` (native invoice flow) or opens `repair.pricing.wizard` for an ad-hoc invoice.
5. An `account.move.action_post()` override detects repair-linked invoices and, after posting, offers a confirmation wizard "Marquer la réparation comme livrée ?". Confirmation transitions the batch to delivered, stamps SAR on repaired lots, creates the workshop → customer stock picking, and marks the pickup appointment done.
6. The old per-manager "Appareil Prêt — à facturer et livrer" activity fan-out is removed; the new UX cues replace it.
7. As a load-bearing pre-step, **`repair.order.batch_id` becomes mandatory**: every repair is auto-wrapped in a singleton batch at creation time, and existing batchless repairs are backfilled by a pre-migration script.

### Out of scope for this sub-project

- **Quote-refused notification path.** Sub-project 2's refusal activity stays as-is. A later iteration can wire its "close" button to `action_notify_client_ready` and reuse sub-project 3's mail machinery.
- **Partial batch pickup** (client takes one device, leaves another). The doctrine stays "one deposit = one batch = one pickup". Operators split batches by hand when necessary.
- **Payment registration.** The invoice is *posted* (not paid) at the moment of delivery. Odoo's native payment widget handles registration separately. "Posted" is the trigger because it matches the user's brief ("facturé → livré").
- **Fully abandoned batches** (every repair `delivery_state='abandoned'`). The batch-ready compute evaluates False, so the flow is simply never triggered.
- **Refactoring `repair.pricing.wizard`.** It continues to be the single entry point for quote and invoice generation. Sub-project 3 only calls it.
- **Multiple reminder mails or client-side cancellation.** Sub-project 1 owns these and they remain unchanged.
- **SMS notifications.** Odoo Community, no IAP, mail only (same constraint as sub-projects 1 and 2).

## Decisions captured from brainstorming

| # | Decision | Why |
|---|---|---|
| 1 | Notification trigger is per-repair but batch-scoped: `action_repair_done` only offers the notify dialog when the acting repair is the last non-terminal in its batch | Avoids noisy notifications, matches sub-project 1's "one batch = one RDV" discipline |
| 2 | Terminal states covered: `done` + `irreparable`; refused-quote path deferred | Matches the user brief ("quand technicien termine la réparation"); irreparable is a technician-driven terminal state with the same pickup semantics; refused-quote already has its own sub-project 2 activity that can own that trigger later |
| 3 | UX: single `Terminer` button + post-transition confirmation dialog, not a split button | Less cognitive load for the technician; server-side check decides when the choice is relevant; no attrs plumbing for button visibility |
| 4 | "Infos supplémentaires" in the mail = existing `repair.order.notes` field | No new field, no wizard input; the technician already writes there during repair work |
| 5 | Fallback "Notifier client" button visible on any done/irreparable repair whose batch is ready but not yet notified, mirrored on the batch form | Covers the "Plus tard" path without cluttering activity feeds |
| 6 | Counter entry point is a "Traiter le retrait" button on the repair/batch form, **not** on the appointment form | The appointment calendar is for planning; the counter workflow starts from what staff looks up when a client arrives (repair or batch reference) |
| 7 | If a sale order exists: route to it; if not: open `repair.pricing.wizard` in invoice mode. Invoice creation uses whichever path is natural; the hook is on `account.move.action_post()` | Reuses native Odoo flows; the wizard only handles the ad-hoc no-quote case; no duplicated invoicing code |
| 8 | `account.move.action_post()` override runs `super()` first, then offers a confirmation wizard "Marquer la réparation comme livrée ?" if pre-conditions match | Keeps posting always-works (hook is post-hoc, never a gate); staff has a confirmation gate to avoid accidental delivery transitions |
| 9 | Per-batch UI, per-repair data for the delivery transition | Staff clicks once; the method loops repairs individually to stamp SAR, create picking, update state |
| 10 | SAR stamping fires on `state='done'` only, **not** on `state='irreparable'` | Nothing was repaired on an irreparable device; no warranty to grant. Last-delivered-repair link still updated for audit |
| 11 | Mail template body expansion lives in `repair_appointment` (existing stub); quote PDF attachment computed in the existing `action_create_pickup_appointment` hook | Sub-project 1 designed this as the integration point; no need to move the template |
| 12 | Quote PDF attached only when `sale.order.state == 'sale'` (accepted quote) | Draft/sent/cancelled quotes are either premature or already-rejected; not worth attaching |
| 13 | Old `mail_act_repair_done` activity fan-out is dropped; post-migration closes open legacy activities | The new UX (dialog + button + batch state + appointment calendar) covers the same need with less noise |
| 14 | `repair.order.batch_id` becomes `required=True`; singleton batches auto-created on repair `create()`; existing batchless repairs backfilled via pre-migration | Load-bearing invariant: the entire sub-project 3 flow (and sub-project 1's appointment model) assumes every repair has a batch. Making it mandatory removes every "if batch else" branch |

## Architecture

All behavior changes live in **`repair_custom`** — the same extension-not-new-addon pattern as sub-project 2. The one cross-module touch is `repair_appointment/data/mail_templates.xml` (body expansion of the existing stub) and `repair_appointment/models/repair_batch.py` (quote attachment in the existing hook).

### Module layout (delta)

```
repair_custom/
├── __manifest__.py                            # MODIFIED: version bump 17.0.1.5.0
├── models/
│   ├── repair_order.py                        # MODIFIED: create() auto-wraps batch,
│   │                                          #           batch_id required=True,
│   │                                          #           action_repair_done returns notify dialog,
│   │                                          #           drop per-manager activity fan-out,
│   │                                          #           SAR stamped only on state='done',
│   │                                          #           action_notify_client_ready_from_repair helper
│   ├── repair_batch.py                        # MODIFIED: ready_for_pickup_notification compute,
│   │                                          #           action_notify_client_ready,
│   │                                          #           action_pickup_start,
│   │                                          #           action_mark_delivered
│   └── account_move.py                        # NEW: action_post() override + delivery prompt
├── wizard/
│   ├── repair_pickup_notify_wizard.py         # NEW: post-completion "notify now?" dialog
│   ├── repair_pickup_deliver_wizard.py        # NEW: post-invoice-post "mark delivered?" dialog
│   └── (existing wizards untouched)
├── views/
│   ├── repair_order_views.xml                 # MODIFIED: Notifier/Traiter le retrait buttons
│   ├── repair_batch_views.xml                 # MODIFIED: mirror buttons + default filter
│   ├── repair_pickup_notify_wizard_views.xml  # NEW
│   └── repair_pickup_deliver_wizard_views.xml # NEW
├── migrations/
│   └── 17.0.1.5.0/
│       ├── pre-migration.py                   # NEW: backfill singleton batches for batchless repairs
│       └── post-migration.py                  # NEW: close legacy mail_act_repair_done activities
├── security/
│   └── ir.model.access.csv                    # MODIFIED: add access rows for the two new wizards
└── tests/
    ├── common.py                              # MODIFIED: fixture extended for pickup flow
    └── test_completion_pickup.py              # NEW

repair_appointment/
├── data/
│   └── mail_templates.xml                     # MODIFIED: expand mail_template_pickup_ready body
└── models/
    └── repair_batch.py                        # MODIFIED: action_create_pickup_appointment attaches quote PDF
```

No new module.

## Section 0 — Mandatory batches (load-bearing pre-step)

### Rationale

The batch model represents the **physical deposit event** — client walks in, hands us N devices. Even a single-device deposit is a deposit event. Sub-project 1 already decided "one batch = one appointment" and spec'd a singleton-wrap for standalone repairs, but the wrap was never implemented. Sub-project 3 requires it as a load-bearing invariant for every downstream method: without it, every batch-level method would need an "if no batch then fall back to repair-level" branch, duplicating the flow.

### Changes

**1. `repair.order.create()` override** in `repair_custom/models/repair_order.py`:

```python
@api.model_create_multi
def create(self, vals_list):
    Batch = self.env['repair.batch']
    for vals in vals_list:
        if not vals.get('batch_id') and vals.get('partner_id'):
            batch = Batch.create({'partner_id': vals['partner_id']})
            vals['batch_id'] = batch.id
    for vals in vals_list:
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('repair.order') or 'New'
    return super(Repair, self).create(vals_list)
```

The batch is created **before** `super()` so the new repair is born with `batch_id` populated and the batch's own `_compute_state` catches it on the first write. The existing `action_add_device_to_batch` flow continues to work unchanged — it still adds a new repair to an existing batch, it just no longer has to create the batch as a side effect in the common case.

**2. Field attr change** in `repair_custom/models/repair_order.py`:

```python
batch_id = fields.Many2one(
    'repair.batch', string="Dossier de Dépôt",
    readonly=True, index=True, ondelete='restrict',
    required=True,
)
```

Stays `readonly=True` (assignment is create-time or via `action_add_device_to_batch`, not hand-edit).

**3. Pre-migration script** `repair_custom/migrations/17.0.1.5.0/pre-migration.py`:

```python
from odoo import api, fields, SUPERUSER_ID

def migrate(cr, version):
    """
    Sub-project 3 mandates one batch per repair. Wrap every existing
    batchless repair in a singleton batch tied to its partner.
    Runs pre-schema so the subsequent NOT NULL constraint finds no
    violating rows.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    Batch = env['repair.batch']
    batchless = env['repair.order'].search([('batch_id', '=', False)])
    for repair in batchless:
        if not repair.partner_id:
            continue  # leave for schema update to flag
        batch = Batch.create({
            'partner_id': repair.partner_id.id,
            'date': repair.entry_date or fields.Datetime.now(),
            'company_id': repair.company_id.id,
        })
        repair.batch_id = batch.id
```

For an artisan shop with a few hundred historical repairs the ORM loop is fast enough and keeps sequences / computes correct.

**4. View filter** in `repair_custom/views/repair_batch_views.xml`:

Add a default search filter "Dossiers multi-appareils" (`repair_count > 1`) on the batch tree view so the sudden influx of singleton batches doesn't clutter the list. Toggleable from the search panel.

### What the rest of sub-project 3 can now assume

- Every `repair.order` has exactly one `batch_id`.
- Every batch has ≥1 repair.
- `batch.ready_for_pickup_notification`, `action_notify_client_ready`, `action_pickup_start`, `action_mark_delivered`, and the `account.move` hook all operate on batches without `if batch_id` guards.
- Sub-project 1's appointment creation always has a batch to attach to.

## Data model

### `repair.batch` — one new stored compute

| Field | Type | Notes |
|---|---|---|
| `ready_for_pickup_notification` | Boolean, computed stored | `True` when: batch has ≥1 repair with `delivery_state != 'abandoned'`; every such repair is in `state ∈ {'done','irreparable'}`; either `current_appointment_id` is empty OR `current_appointment_id.notification_sent_at` is `False`. Depends on `repair_ids.state`, `repair_ids.delivery_state`, `appointment_ids.state`, `appointment_ids.notification_sent_at` |

The compute captures the full readiness contract. Its `True → False` transition happens exactly once per batch lifecycle (when the notification is sent), matching the "one notification per batch" doctrine.

### `repair.order` — one related field for view visibility

| Field | Type | Notes |
|---|---|---|
| `batch_ready_for_pickup_notification` | Boolean, `related='batch_id.ready_for_pickup_notification'`, `store=False` | Powers `attrs="invisible"` on the fallback "Notifier client" button |

No new persistent field for "already notified" on the repair. That state lives on `batch.current_appointment_id.notification_sent_at` — single source of truth.

### `repair.pickup.notify.wizard` (new TransientModel)

| Field | Type | Notes |
|---|---|---|
| `batch_id` | M2O `repair.batch`, required, readonly | The batch whose client should be notified |
| `partner_name` | Char, related `batch_id.partner_id.name`, readonly | Displayed in the dialog text |
| `repair_count` | Integer, related `batch_id.repair_count`, readonly | Displayed in the dialog text |

Methods:
- `action_send()` — calls `batch.action_notify_client_ready()` and closes.
- `action_postpone()` — closes; the fallback button remains visible because the batch-ready state is unchanged.

### `repair.pickup.deliver.wizard` (new TransientModel)

| Field | Type | Notes |
|---|---|---|
| `batch_id` | M2O `repair.batch`, required, readonly | Batch whose repairs are being delivered |
| `invoice_id` | M2O `account.move`, required, readonly | The invoice that was just posted (audit trail only) |
| `partner_name` | Char, related | Displayed in the dialog |
| `repair_ids` | M2M `repair.order`, computed readonly | Filtered subset: non-abandoned, delivery `none`, state in done/irreparable |

Methods:
- `action_confirm()` — calls `batch.action_mark_delivered()` and closes.
- `action_dismiss()` — closes; staff can retrigger via the "Traiter le retrait" button if they change their mind.

## Section 1 — Architecture summary

See above (Module layout + Data model). The architecture has three load-bearing pillars:

1. **Mandatory batches** (Section 0) — invariant that makes every downstream method batch-scoped.
2. **`ready_for_pickup_notification` compute on `repair.batch`** — single source of truth for "is this batch waiting for a notification?".
3. **`account.move.action_post()` hook** — the only automation that decides when `delivery_state` flips. Everything else is explicit staff action.

## Section 2 — Completion flow

### `action_repair_done` (modified)

```python
def action_repair_done(self):
    # Existing guards (abandoned check, quote_required validation,
    # FOR UPDATE lock) are unchanged.

    # Existing transition
    res = self.write({
        'state': 'done',
        'parts_waiting': False,
        'end_date': fields.Datetime.now(),
    })

    # Existing cleanup of validate-quote activities — unchanged
    quote_act_type = self.env.ref(
        'repair_custom.mail_act_repair_quote_validate', raise_if_not_found=False,
    )
    if quote_act_type:
        self.activity_ids.filtered(
            lambda a: a.activity_type_id == quote_act_type
        ).action_feedback(feedback="Clôture automatique : Réparation terminée.")

    # REMOVED: the per-manager "Appareil Prêt" activity fan-out
    # (mail_act_repair_done). The new UX replaces it.

    # NEW: post-transition notify dialog
    self.env.flush_all()  # ensure stored compute recalculated
    ready_batches = self.mapped('batch_id').filtered('ready_for_pickup_notification')
    if (ready_batches
            and not self.env.context.get('skip_pickup_notify_prompt')
            and len(ready_batches) == 1
            and len(self) == 1):
        return {
            'name': _("Dossier prêt pour retrait"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.pickup.notify.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_batch_id': ready_batches.id},
        }

    return res
```

The dialog only fires on the single-repair single-batch interactive path. Bulk updates, cron runs, and tests with `skip_pickup_notify_prompt=True` skip it. The fallback button handles the skipped cases.

### `batch.action_notify_client_ready` (new)

```python
def action_notify_client_ready(self):
    """
    Trigger the initial pickup-ready notification for this batch.

    Idempotent: if the batch already has a non-terminal appointment
    with notification_sent_at set, this is a no-op returning the
    existing appointment. Otherwise delegates to sub-project 1's
    `action_create_pickup_appointment(notify=True)`, which creates
    the appointment and sends mail_template_pickup_ready.

    Called from:
      - repair.pickup.notify.wizard.action_send (post-completion dialog)
      - the "Notifier client" fallback button on repair and batch forms
    """
    self.ensure_one()
    if not self.ready_for_pickup_notification:
        raise UserError(_(
            "Ce dossier n'est pas prêt pour une notification de retrait."
        ))
    return self.action_create_pickup_appointment(notify=True)
```

### `repair.action_notify_client_ready_from_repair` (new helper)

```python
def action_notify_client_ready_from_repair(self):
    self.ensure_one()
    if not self.batch_id:
        raise UserError(_("Cette réparation n'est pas liée à un dossier."))
    return self.batch_id.action_notify_client_ready()
```

Thin wrapper so the repair form button can fire the batch-level action without the XML having to reach `batch_id.action_notify_client_ready` through a button name.

### Views — notify dialog

```xml
<record id="view_repair_pickup_notify_wizard_form" model="ir.ui.view">
    <field name="name">repair.pickup.notify.wizard.form</field>
    <field name="model">repair.pickup.notify.wizard</field>
    <field name="arch" type="xml">
        <form>
            <p>
                Le dossier <field name="batch_id" readonly="1"/>
                (<field name="repair_count"/> appareil(s)) est maintenant complet.
            </p>
            <p>Souhaitez-vous notifier
                <field name="partner_name" readonly="1"/>
                que son dossier est prêt pour le retrait ?
            </p>
            <footer>
                <button name="action_send" type="object"
                        string="Envoyer la notification" class="btn-primary"/>
                <button name="action_postpone" type="object"
                        string="Plus tard" class="btn-secondary"/>
            </footer>
        </form>
    </field>
</record>
```

### Views — fallback button on repair form header

```xml
<button name="action_notify_client_ready_from_repair"
        type="object"
        string="Notifier client"
        class="btn-secondary"
        invisible="not batch_ready_for_pickup_notification"/>
```

### Views — mirror button on batch form header

```xml
<button name="action_notify_client_ready"
        type="object"
        string="Notifier client – dossier prêt"
        class="btn-primary"
        invisible="not ready_for_pickup_notification"/>
```

## Section 3 — Counter pickup flow

### Entry point: "Traiter le retrait" button

Visible on both the repair form and the batch form when the batch has at least one non-abandoned, non-delivered repair in `done`/`irreparable`.

Repair form XML:

```xml
<button name="action_pickup_start"
        type="object"
        string="Traiter le retrait"
        class="btn-primary"
        invisible="delivery_state != 'none' or not batch_ready_for_pickup_notification"/>
```

On the repair, `action_pickup_start` is a thin wrapper calling `self.batch_id.action_pickup_start()`.

### `batch.action_pickup_start` (new — router)

```python
def action_pickup_start(self):
    """
    Counter entry point. Routes to:
      - the linked sale.order (native invoice flow) if one exists
      - the repair.pricing.wizard in invoice mode otherwise

    Invoice creation happens where it happens. The post-invoice
    transition to 'delivered' is driven by the account.move hook,
    not by this method.
    """
    self.ensure_one()
    eligible = self.repair_ids.filtered(
        lambda r: r.delivery_state == 'none'
                  and r.state in ('done', 'irreparable')
    )
    if not eligible:
        raise UserError(_("Aucune réparation en attente de livraison dans ce dossier."))

    sale_orders = self.repair_ids.mapped('sale_order_id')
    if sale_orders:
        return {
            'name': _("Devis / Bon de Commande"),
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': sale_orders[:1].id,
            'view_mode': 'form',
            'target': 'current',
        }

    return {
        'name': _("Facturation Atelier"),
        'type': 'ir.actions.act_window',
        'res_model': 'repair.pricing.wizard',
        'view_mode': 'form',
        'target': 'new',
        'context': {
            'default_repair_id': eligible[:1].id,
            'default_mode': 'invoice',
        },
    }
```

The `default_mode='invoice'` key is a proposed addition to `repair.pricing.wizard`. If the wizard doesn't already honor a mode flag, the implementation plan adds a one-line default. No behavioral change to wizard logic.

### `account.move.action_post()` override

```python
class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_post(self):
        res = super().action_post()

        if self.env.context.get('skip_repair_pickup_transition'):
            return res

        candidate_repairs = self.env['repair.order']
        for move in self:
            if move.move_type != 'out_invoice':
                continue
            repairs = move.repair_id
            if not repairs:
                # Fallback: walk sale.order link for invoices created
                # via the native sale.order flow.
                repairs = move.invoice_line_ids.mapped(
                    'sale_line_ids.order_id.repair_order_ids'
                )
            candidate_repairs |= repairs.filtered(
                lambda r: r.state in ('done', 'irreparable')
                          and r.delivery_state == 'none'
            )

        if not candidate_repairs:
            return res

        batches_with_work = candidate_repairs.mapped('batch_id').filtered(
            lambda b: any(
                r.delivery_state == 'none'
                and r.state in ('done', 'irreparable')
                for r in b.repair_ids
            )
        )

        if len(batches_with_work) != 1 or len(self) != 1:
            # Multi-invoice or multi-batch post: skip the prompt.
            # Staff can use the "Traiter le retrait" button manually.
            return res

        return {
            'name': _("Marquer la réparation comme livrée ?"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.pickup.deliver.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_batch_id': batches_with_work.id,
                'default_invoice_id': self.id,
            },
        }
```

Design invariants:
- `super().action_post()` runs first. The invoice is always posted. The prompt is post-hoc, never a gate.
- `skip_repair_pickup_transition=True` in context disables the prompt entirely (used by cron, data migrations, tests that don't care).
- Narrow scope: single-invoice, single-batch, interactive context. Everything else falls back to manual "Traiter le retrait".
- Uses `move.repair_id` (the `invoice_ids` inverse already on `repair.order`) first, falling back to walking sale_line → order → repair_order_ids for invoices created via the native Odoo flow.

### Delivery dialog view

```xml
<record id="view_repair_pickup_deliver_wizard_form" model="ir.ui.view">
    <field name="name">repair.pickup.deliver.wizard.form</field>
    <field name="model">repair.pickup.deliver.wizard</field>
    <field name="arch" type="xml">
        <form>
            <p>
                La facture <field name="invoice_id" readonly="1"/> vient d'être validée.
            </p>
            <p>
                Souhaitez-vous marquer les appareils de
                <field name="partner_name" readonly="1"/>
                comme livrés et démarrer leur garantie retour (SAR) ?
            </p>
            <field name="repair_ids" readonly="1">
                <tree>
                    <field name="name"/>
                    <field name="device_id_name"/>
                    <field name="state"/>
                </tree>
            </field>
            <footer>
                <button name="action_confirm" type="object"
                        string="Confirmer la livraison" class="btn-primary"/>
                <button name="action_dismiss" type="object"
                        string="Plus tard" class="btn-secondary"/>
            </footer>
        </form>
    </field>
</record>
```

### `batch.action_mark_delivered` (new — single transition point)

```python
def action_mark_delivered(self):
    """
    Transition all eligible repairs in this batch to delivered and
    run the side effects:
      - stamp sar_expiry on the lot (done only, NOT irreparable)
      - create the workshop → customer stock picking
      - mark the linked pickup appointment as done
      - post a chatter note on the batch

    Per-batch UI, per-repair data.
    """
    self.ensure_one()
    to_deliver = self.repair_ids.filtered(
        lambda r: r.delivery_state == 'none'
                  and r.state in ('done', 'irreparable')
    )
    if not to_deliver:
        raise UserError(_("Aucune réparation à livrer dans ce dossier."))

    to_deliver.action_repair_delivered()

    if self.current_appointment_id and self.current_appointment_id.state == 'scheduled':
        self.current_appointment_id.action_mark_done()

    self.message_post(body=_(
        "Dossier livré : %d appareil(s) remis au client."
    ) % len(to_deliver))
```

### SAR stamping — done only

In `action_repair_delivered` (existing method), the current block:

```python
sar_months = self._get_sar_warranty_months()
for rec in self:
    if rec.lot_id:
        sar_expiry = fields.Date.today() + relativedelta(months=sar_months)
        rec.lot_id.write({
            'last_delivered_repair_id': rec.id,
            'sar_expiry': sar_expiry,
        })
```

becomes:

```python
sar_months = self._get_sar_warranty_months()
for rec in self:
    if not rec.lot_id:
        continue
    if rec.state != 'done':
        # Irreparable: no SAR — nothing was repaired. Still update
        # last_delivered_repair_id so history tracks the event.
        rec.lot_id.write({'last_delivered_repair_id': rec.id})
        continue
    sar_expiry = fields.Date.today() + relativedelta(months=sar_months)
    rec.lot_id.write({
        'last_delivered_repair_id': rec.id,
        'sar_expiry': sar_expiry,
    })
```

One focused change, no refactor.

### Edge cases

- **No appointment at all** (tech chose "Plus tard" in the notify dialog, client walked in without ever receiving a mail): pickup flow still works end-to-end. `action_mark_delivered` sees `current_appointment_id` is empty and skips the appointment-done call.
- **Invoice posted in advance**: the pre-conditions on the hook (`state ∈ {done, irreparable}` + `delivery_state='none'` + single batch) exclude most accidental early-post scenarios. For edge cases, admins can pass `skip_repair_pickup_transition=True`.
- **Multi-invoice batch**: if the batch has two separate posted invoices, the hook fires independently for each. The second post will find the first invoice's work already delivered and exit cleanly.

## Section 4 — Mail template expansion

### Body expansion

Edit `repair_appointment/data/mail_templates.xml`, record `mail_template_pickup_ready`. Model stays `repair.pickup.appointment`. Body reaches into `object.batch_id.repair_ids` for the device loop.

```xml
<field name="body_html" type="html">
    <div style="font-family: Arial, sans-serif; color: #222;">
        <p>Bonjour <t t-out="object.partner_id.name or ''"/>,</p>

        <p>Bonne nouvelle : votre dossier
            <strong t-out="object.batch_id.name or ''"/>
            est prêt pour le retrait.
        </p>

        <h3>Récapitulatif de votre dépôt</h3>
        <ul>
            <li>
                <strong>Date de dépôt :</strong>
                <t t-out="object.batch_id.date and object.batch_id.date.strftime('%d/%m/%Y') or ''"/>
            </li>
            <li>
                <strong>Lieu de retrait :</strong>
                <t t-out="object.location_id.name or ''"/>
            </li>
        </ul>

        <h3>Appareils</h3>
        <t t-foreach="object.batch_id.repair_ids.filtered(lambda r: r.delivery_state != 'abandoned' and r.state in ('done', 'irreparable'))" t-as="repair">
            <div style="margin-bottom: 16px; padding: 12px; border-left: 3px solid #875A7B;">
                <p><strong t-out="repair.device_id_name or ''"/></p>

                <t t-if="repair.tag_ids">
                    <p><em>Pannes constatées :</em>
                        <t t-out="', '.join(repair.tag_ids.mapped('name'))"/>
                    </p>
                </t>

                <t t-if="repair.state == 'done' and repair.internal_notes">
                    <p><em>Intervention :</em></p>
                    <p t-out="repair.internal_notes or ''"/>
                </t>
                <t t-if="repair.state == 'irreparable'">
                    <p><em>Diagnostic :</em> cet appareil n'a pas pu être réparé.</p>
                    <t t-if="repair.internal_notes">
                        <p t-out="repair.internal_notes or ''"/>
                    </t>
                </t>

                <t t-if="repair.notes">
                    <p><em>Informations complémentaires :</em></p>
                    <p t-out="repair.notes or ''"/>
                </t>
            </div>
        </t>

        <p>
            Pour choisir le créneau qui vous convient le mieux, cliquez sur
            le lien ci-dessous :
        </p>
        <p style="text-align: center; margin: 24px 0;">
            <a t-att-href="object._portal_url()"
               style="background-color: #875A7B; color: white; padding: 12px 24px;
                      text-decoration: none; border-radius: 4px; display: inline-block;">
                Prendre rendez-vous
            </a>
        </p>

        <p>À très bientôt,<br/>L'équipe de réparation</p>
    </div>
</field>
```

### Quote PDF attachment

Modify `action_create_pickup_appointment` in `repair_appointment/models/repair_batch.py` to compute and pass the attachment at send time:

```python
def action_create_pickup_appointment(self, notify=True):
    self.ensure_one()
    if self.current_appointment_id:
        return self.current_appointment_id
    apt = self.env['repair.pickup.appointment'].create({'batch_id': self.id})
    if notify:
        template = self.env.ref(
            'repair_appointment.mail_template_pickup_ready',
            raise_if_not_found=False,
        )
        if template:
            attachment_ids = self._build_pickup_quote_attachments()
            email_values = None
            if attachment_ids:
                email_values = {'attachment_ids': attachment_ids}
            template.send_mail(
                apt.id, force_send=False, email_values=email_values,
            )
            apt.notification_sent_at = fields.Datetime.now()
    return apt

def _build_pickup_quote_attachments(self):
    """
    Return a list of ir.attachment ids to attach to the pickup-ready mail:
      - the PDF of the linked sale.order, only if it exists and is
        in state 'sale' (accepted quote)
      - empty list otherwise
    """
    self.ensure_one()
    sale_orders = self.repair_ids.mapped('sale_order_id').filtered(
        lambda s: s.state == 'sale'
    )
    if not sale_orders:
        return []
    report = self.env.ref('sale.action_report_saleorder')
    pdf_content, _ = report._render_qweb_pdf(
        report.report_name, sale_orders[:1].ids,
    )
    attachment = self.env['ir.attachment'].create({
        'name': _("Devis %s.pdf") % sale_orders[:1].name,
        'type': 'binary',
        'datas': base64.b64encode(pdf_content),
        'res_model': 'sale.order',
        'res_id': sale_orders[:1].id,
        'mimetype': 'application/pdf',
    })
    return [attachment.id]
```

This edit lives in `repair_appointment` because `action_create_pickup_appointment` is the integration point sub-project 1 designed. `_build_pickup_quote_attachments` returns an empty list when no `sale.state='sale'` quote exists, so sub-project 1's own tests continue to pass unchanged.

## Removed legacy flow

The per-manager fan-out in `action_repair_done`:

```python
pickup_type = self.env.ref('repair_custom.mail_act_repair_done', ...)
for manager_user in group_manager.users:
    activities_to_create.append({...})
```

is deleted. The cleanup of those activities in `action_repair_delivered` becomes a harmless no-op for new-flow repairs; it still runs to handle edge cases where an admin manually created one.

Post-migration closes legacy open activities:

```python
# repair_custom/migrations/17.0.1.5.0/post-migration.py
from odoo import api, SUPERUSER_ID

def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    act_type = env.ref('repair_custom.mail_act_repair_done', raise_if_not_found=False)
    if not act_type:
        return
    open_activities = env['mail.activity'].search([
        ('activity_type_id', '=', act_type.id),
        ('res_model', '=', 'repair.order'),
    ])
    for act in open_activities:
        try:
            act.action_feedback(
                feedback="Clôture automatique — flux de livraison refondu (sous-projet 3)"
            )
        except Exception:
            act.unlink()
```

The activity type record itself stays in `mail_activity_type_data.xml` to avoid breaking old data references; no new code creates activities of that type.

## Testing strategy

### Unit and integration tests

All in `repair_custom/tests/test_completion_pickup.py` except where noted.

| Test | What it covers |
|---|---|
| `test_create_repair_auto_wraps_batch` | Creating a repair with `partner_id` but no `batch_id` populates `batch_id` with a fresh singleton batch |
| `test_create_repair_with_batch_keeps_batch` | Creating a repair with an explicit `batch_id` does not create a new batch |
| `test_action_add_device_to_batch_unchanged` | Existing `action_add_device_to_batch` still adds a new repair to the existing batch |
| `test_batch_id_required_constraint` | Attempting to write `batch_id = False` on an existing repair raises |
| `test_pre_migration_wraps_batchless_repairs` | Simulates the pre-upgrade state (null `batch_id`), runs the migrate function, asserts the repair ends with a valid batch |
| `test_ready_for_pickup_notification_compute` | Stored compute flips True when last non-abandoned repair hits done, False when already notified, False when every repair is abandoned, False during partial progress |
| `test_action_repair_done_dialog_on_last` | Calling `action_repair_done` on the last non-terminal repair returns the notify wizard action dict |
| `test_action_repair_done_no_dialog_bulk` | Bulk `action_repair_done` across multiple repairs skips the dialog |
| `test_action_notify_client_ready_idempotent` | Calling the action twice creates only one appointment and sends only one mail |
| `test_notify_readiness_guard` | `action_notify_client_ready` raises when `ready_for_pickup_notification` is False |
| `test_action_pickup_start_with_sale_order` | Returns an `act_window` targeting the linked sale.order |
| `test_action_pickup_start_without_sale_order` | Returns an `act_window` opening `repair.pricing.wizard` in invoice mode |
| `test_account_move_action_post_prompt` | Posting a repair-linked customer invoice returns the delivery wizard action dict |
| `test_account_move_action_post_skip_context` | Same post with `skip_repair_pickup_transition=True` returns normally |
| `test_account_move_action_post_multi_batch_skip` | Multi-invoice/multi-batch post does not open the wizard |
| `test_action_mark_delivered_per_batch` | Transitions all eligible repairs, skips abandoned, stamps SAR on done only, creates customer picking, marks appointment done |
| `test_irreparable_no_sar` | Irreparable repair delivery leaves `lot.sar_expiry` untouched but updates `last_delivered_repair_id` |
| `test_no_appointment_pickup` | When the "Plus tard" path leaves the batch without an appointment, the pickup flow still succeeds end-to-end |
| `test_legacy_activity_migration` | Post-migration closes all open `mail_act_repair_done` activities on existing repairs |
| `test_mail_template_pickup_ready_body` *(in `repair_appointment/tests/`)* | Render the template against a fixture batch with done + irreparable repairs; assert device names present, intervention block only on done, portal URL present |
| `test_mail_template_pickup_ready_attachment` *(in `repair_appointment/tests/`)* | `_build_pickup_quote_attachments` returns non-empty list when batch has a sale-state SO; empty when state is draft/sent/cancelled |

### Test fixture

`repair_custom/tests/common.py` extends the sub-project 2 fixture:
- Two partners (multi-device and single-device)
- Batch with two repairs: one `done` with tags and notes, one `irreparable` with notes
- One standalone repair (auto-wrapped in a singleton batch)
- A confirmed `sale.order` linked to the multi-device batch
- Technician user, manager user, admin user (reused from sub-project 2)
- Pickup locations and default appointment schedule from sub-project 1 data

### Manual QA checklist

1. Upgrade the module; verify existing batchless repairs get singleton batches (pre-migration).
2. Verify open legacy `Appareil Prêt` activities are closed with the feedback line (post-migration).
3. Create a new repair, confirm `batch_id` auto-populates.
4. Create a second repair for the same client via `action_add_device_to_batch`; confirm both share the batch.
5. Mark one repair `done` — no dialog.
6. Mark the second repair `done` — notify dialog appears. Click "Plus tard".
7. Verify the fallback "Notifier client" button is visible on both repairs and on the batch form.
8. Click the fallback button on the batch; verify appointment created + mail sent.
9. Open the client's pickup portal via the token; verify the summary email rendered correctly in Outlook / Gmail / Apple Mail.
10. Book a slot from the portal.
11. Back in Odoo, click "Traiter le retrait" on the batch; verify routing to the linked sale.order.
12. Create and post the invoice from the sale.order; verify the delivery dialog appears.
13. Confirm the delivery; verify all repairs flip to `delivered`, SAR stamped on the `done` lots (not irreparable), appointment marked `done`, stock picking created workshop→customer.
14. Repeat steps 11–13 with a batch that has no sale.order — verify the pricing wizard opens in invoice mode.

## Migrations

`repair_custom/__manifest__.py` version bump from `17.0.1.4.0` to `17.0.1.5.0`.

- `repair_custom/migrations/17.0.1.5.0/pre-migration.py` — backfills singleton batches for batchless repairs.
- `repair_custom/migrations/17.0.1.5.0/post-migration.py` — closes open legacy `mail_act_repair_done` activities.

No schema change required beyond the `batch_id` NOT NULL constraint and the new stored compute. Odoo's ORM handles the rest during upgrade.

## Git branch

`feature/repair-completion-pickup` from `main` (after sub-project 2's `feature/repair-quote-lifecycle` is merged — which it should be before sub-project 3 starts, to avoid a three-way merge).

## Open questions / deferred decisions

None blocking. Three items to note for future follow-ups:

1. **Quote-refused notification path.** Sub-project 2 opens a `mail_act_repair_quote_refused` activity; a later iteration can wire that activity's close button to call `batch.action_notify_client_ready`, reusing sub-project 3's mail machinery for free.
2. **Partial batch pickup.** Doctrine stays "one deposit = one batch = one pickup". If real-world frequency pushes back on this, consider a `split_batch` action later.
3. **Multi-invoice batches.** A batch whose manager ran the pricing wizard multiple times could end up with several linked invoices; the post hook handles each independently. The second post exits cleanly because pre-conditions no longer match. Acceptable for now.
