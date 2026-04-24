# QA 2026-04-24 — Follow-up Tracks A & C

**Context:** Triage of QA findings from `QA-checklist-2026-04-24.md` split into three tracks.
Track B (quote lifecycle polish) is being worked on separately. This file carries
enough context to pick up Tracks A and C in later sessions without re-triaging.

---

## Track A — Feature 3 (Device/Lot) propagation bugs

### A1. Quick-create on `lot_id` opens the full stock.lot form

**Expected (spec `2026-04-23-device-lot-edit-propagation-design.md`):**
Typing a brand-new serial in the repair's `lot_id` field and tabbing out should
create the `stock.lot` on the fly (inline quick-create), linked to the current
product template, with `is_hifi_unit=True`. No modal form.

**Observed:** The stock.lot form opens instead of inline creation.

**Where to look:**
- `repair_custom/views/repair_views.xml:387-393` — the **existing-devices picker**
  currently has `options="{'no_create': True, 'no_open': True}"`. This is NOT the
  main intake field; it is gated by `show_lot_field` and domain-scoped to the
  partner's existing lots.
- The **primary intake `lot_id`** field (the one that should auto-create) must
  be elsewhere in the form. Grep for `lot_id` occurrences in repair_views.xml
  and identify which one is the intake. Add `options="{'quick_create': True}"`
  or confirm Odoo 17 M2O quick-create behavior — may need a `name_create`
  override on `stock.lot` to stamp `is_hifi_unit=True` and product linkage.
- Check `repair_custom/models/repair_extensions.py` for existing `name_create`
  hooks on `stock.lot`.

**Acceptance:** Type "NEW-SERIAL-123" in the intake `lot_id` field → tab out →
new stock.lot row exists with `name='NEW-SERIAL-123'`, `product_id` set,
`is_hifi_unit=True`. No modal opens.

### A2. `category_short_name` does not regenerate when `product.template.categ_id` is edited

**Root cause (confirmed):**
- `repair_custom/models/repair_order.py:401` — `category_short_name` is
  `compute='_compute_category_short_name'` (line 411).
- The compute depends on **`category_id`** (local field on `repair.order`),
  NOT on `product_tmpl_id.categ_id`.
- `category_id` is assigned via an **onchange** at line 431-435, which only
  fires when the user changes `product_tmpl_id` in the form view. Editing the
  template's category elsewhere never propagates.

**Two fix options (pick one during design):**
1. **Make `category_id` related to `product_tmpl_id.categ_id`** (store=True,
   readonly=True). Simplest; category_short_name compute then reacts correctly.
   Risk: any code that writes `category_id` directly breaks — grep for writes.
2. **Drop `category_id` from repair.order**, compute `category_short_name`
   directly off `product_tmpl_id.categ_id` and `.parent_id`. Cleaner but larger
   view surgery (forms, search, filters, reports may reference `category_id`).

**Where to look:**
- `grep -rn "category_id" repair_custom/ | grep -v ".pyc"` — see every writer/reader.
- Report templates: confirm `category_short_name` reads are the field itself,
  not `category_id.name` (already OK per `10ce782` and `16a296f` commits).

**Acceptance:** In tab A, open a confirmed repair. In tab B, change the
template's `categ_id`. Refresh the repair tree/form → `category_short_name`
reflects the new category without any onchange or recompute button.

---

## Track C — Missing Spec Items

### C1. Admin-only "Renvoyer la notification initiale" button on appointment form

**Expected (QA checklist §4 "Admin-only"):**
A developer/admin-only button on `repair.pickup.appointment` form that re-fires
the **initial ready-for-pickup mail** (same template used when the batch is
first marked ready). Distinct from the existing "Renvoyer le rappel" reminder
button.

**Current state:**
- `repair_appointment/views/appointment_views.xml:47-49` — button
  `action_send_reminder_now` labeled "Renvoyer le rappel" exists but:
  - Fires the **reminder** template, not the initial notification.
  - Not gated to `group_repair_admin`.
  - Only visible when `state='pending'`.

**Design questions for later session:**
- Which mail template is the "initial ready-for-pickup" one? (Look in
  `repair_custom/data/mail_templates.xml` or `repair_appointment/data/`.
  Likely the one sent by `action_notify_ready` / batch completion dialog.)
- Button label: "Renvoyer la notification initiale" per QA checklist.
- Visibility: `groups="repair_custom.group_repair_admin"` AND any state where
  resending makes sense (pending, scheduled — not done/cancelled/no_show).
- Add new method `action_resend_initial_notification` on
  `repair.pickup.appointment` that resolves the template ref and calls
  `template.send_mail(self.id, force_send=True)`; posts chatter note.

**Where to implement:**
- View: `repair_appointment/views/appointment_views.xml` header block.
- Model method: `repair_appointment/models/repair_pickup_appointment.py`.

### C2. "Traiter le retrait" button — MISSING entirely (feature gap)

**Context:** This is an unimplemented task from plan
`2026-04-11-repair-completion-pickup.md` Task E3 (lines 1244-1287).
Spec: `2026-04-11-repair-completion-pickup-design.md:367-490`.

**Original goal (from spec §6):**
> The appointment calendar is for planning; the counter workflow starts from
> what staff looks up when a client arrives (repair or batch reference).

When a client walks into the counter:
1. Staff opens the repair or batch form.
2. Clicks **"Traiter le retrait"**.
3. Button routes to:
   - Linked `sale.order` form (native invoice flow) if an **approved quote** exists.
   - `repair.pricing.wizard` in invoice mode if **no quote** (walk-in ad hoc case).
4. Invoice posted → existing delivery dialog fires → delivered state, SAR stamped,
   picking validated, appointment marked done.

**Visibility rules (from spec):**
- Visible when `delivery_state='none'` AND `state in ('done', 'irreparable')`.
- Batch-level button: routes to first eligible repair's path (spec defines
  precedence when batch has mixed states).

**Where to implement:**
- Model methods on `repair.order` AND `repair.batch`:
  - `action_process_pickup()` — chooses SO vs wizard path, returns act_window.
- Views: `repair_custom/views/repair_views.xml` (repair form header) +
  `repair_custom/views/repair_batch_views.xml` (batch form header).
- Plan Task E3 already specifies button XML, action method skeleton, tests.
  Re-read that plan section as the starting point — much of the design is done.

**Test plan already exists:**
- `repair_custom/tests/test_completion_pickup.py:482` references the walk-in
  flow — confirm the test exists but was xfailed or similar; it expects the
  button to exist.

**Why it matters:**
Without this button, staff have no guided path from "client at counter" to
"invoice + delivery". They must manually navigate to SO or pricing wizard.
End-to-end Scenario A (QA checklist §9) is blocked at step 8.

---

## Suggested session structure for later work

- **Track A session:** ~1-2 hours. Both bugs are small-scope. Single branch
  `fix/device-lot-propagation-qa`. Brainstorm → plan → implement → test.
- **Track C session:** ~2-4 hours. C2 is the bulk (feature implementation from
  existing plan). Single branch `feature/counter-pickup-button`. Re-read
  plan E3 first, confirm what changed since 2026-04-11, then implement.

## Cross-reference

- Full QA notes: `docs/superpowers/QA-checklist-2026-04-24.md`
- Device/lot spec: `docs/superpowers/specs/2026-04-23-device-lot-edit-propagation-design.md`
- Completion-pickup spec: `docs/superpowers/specs/2026-04-11-repair-completion-pickup-design.md`
- Completion-pickup plan (Task E3): `docs/superpowers/plans/2026-04-11-repair-completion-pickup.md:1244`
