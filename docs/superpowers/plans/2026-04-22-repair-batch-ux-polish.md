# Repair Batch/Repair UX & Lifecycle Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish five day-to-day UX & lifecycle pain points around `repair.order` ↔ `repair.batch` that emerged after the three-sub-project overhaul shipped: deferred batch creation, aggregated batch delivery state + bulk `Livrer`, sibling-repair banner, archive cascade, and manager navigation bridge.

**Architecture:** All changes live inside `repair_custom` (extension, no new addon). Python edits concentrate in `models/repair_order.py` and `models/repair_batch.py`; XML edits in `views/repair_views.xml` and `views/repair_batch_views.xml`. No schema migration — only a NOT NULL constraint drop on `repair_order.batch_id`. No data migration (sub-project 3's pre-migration already backfilled historical rows).

**Tech Stack:** Odoo 17, Python 3.10+, PostgreSQL. Tests with `odoo.tests.common.TransactionCase`. Python env activated via `workon odoo_dev` (pyenv virtualenvwrapper).

**Branch:** `feature/repair-batch-ux-polish` (already created from `main`; spec committed as `34302e0`).

**Spec:** `docs/superpowers/specs/2026-04-22-repair-batch-ux-polish-design.md`.

**Test runner:** From the Odoo root (`/Users/martin/Documents/odoo_dev/odoo`):
```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestBatchUxPolish
```
(Replace `TestBatchUxPolish` with the specific class for faster iteration.)

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `repair_custom/models/repair_order.py` | Modified | Defer batch creation to `action_confirm`; archive cascade on unlink/write; sibling banner fields; smart-button count; batch_id required-constraint relaxation |
| `repair_custom/models/repair_batch.py` | Modified | Aggregated `delivery_state` stored compute |
| `repair_custom/views/repair_views.xml` | Modified | Sibling-repair banner; `batch_id` column on tree; `group_by_batch` filter; `decoration-success`; smart-button label |
| `repair_custom/views/repair_batch_views.xml` | Modified | `delivery_state` column + badge; `decoration-success`; `Livrer` header button; default filter pinned to `repair_count > 1` |
| `repair_custom/__manifest__.py` | Modified | Version bump `17.0.1.5.0` → `17.0.1.6.0` |
| `repair_custom/tests/test_batch_ux_polish.py` | New | All new tests for the five sections |

---

## Task 1: Defer batch creation to `action_confirm`

**Files:**
- Modify: `repair_custom/models/repair_order.py:950-965` (remove create-time wrap, relax constraint)
- Modify: `repair_custom/models/repair_order.py:492-500` (drop schema-level `required=True` — the field currently doesn't have it, only the `@api.constrains`; confirm during edit)
- Modify: `repair_custom/models/repair_order.py` — `action_confirm` (find by `grep -n "def action_confirm" repair_custom/models/repair_order.py`)
- Test: `repair_custom/tests/test_batch_ux_polish.py`

- [ ] **Step 1.1: Scaffold the test file with common fixture**

Create `repair_custom/tests/test_batch_ux_polish.py`:

```python
# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('-at_install', 'post_install', 'repair_custom')
class RepairBatchUxCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Test Partner UX'})
        cls.product_tmpl = cls.env['product.template'].create({
            'name': 'UX Test Device',
            'type': 'product',
            'tracking': 'serial',
        })
        cls.Repair = cls.env['repair.order']
        cls.Batch = cls.env['repair.batch']

    def _new_draft_repair(self, **overrides):
        vals = {
            'partner_id': self.partner.id,
            'product_tmpl_id': self.product_tmpl.id,
        }
        vals.update(overrides)
        return self.Repair.create(vals)
```

Also add the test file to `repair_custom/tests/__init__.py`:

```python
from . import test_batch_ux_polish
```

- [ ] **Step 1.2: Register the new test module in `tests/__init__.py`**

Read `repair_custom/tests/__init__.py` and append:

```python
from . import test_batch_ux_polish
```

- [ ] **Step 1.3: Write failing test for "draft repair has no batch"**

Add to `test_batch_ux_polish.py`:

```python
@tagged('-at_install', 'post_install', 'repair_custom')
class TestDeferredBatchCreation(RepairBatchUxCommon):
    def test_create_repair_without_batch(self):
        repair = self._new_draft_repair()
        self.assertFalse(
            repair.batch_id,
            "Draft repair must not have a batch populated at create()",
        )
        self.assertEqual(repair.state, 'draft')
```

- [ ] **Step 1.4: Run test, expect FAIL**

From `/Users/martin/Documents/odoo_dev/odoo`:

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestDeferredBatchCreation
```

Expected: test `test_create_repair_without_batch` fails because `create()` currently auto-wraps the batch (see repair_order.py:950-955).

- [ ] **Step 1.5: Remove create-time batch wrap**

In `repair_custom/models/repair_order.py` around line 950, change:

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

to:

```python
@api.model_create_multi
def create(self, vals_list):
    for vals in vals_list:
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('repair.order') or 'New'
    return super(Repair, self).create(vals_list)
```

- [ ] **Step 1.6: Relax the batch_id `@api.constrains`**

In `repair_custom/models/repair_order.py` around line 962, change:

```python
@api.constrains('batch_id')
def _check_batch_id_required(self):
    for rec in self:
        if not rec.batch_id:
            raise ValidationError(_("Un dossier de dépôt est obligatoire pour chaque réparation."))
```

to:

```python
@api.constrains('batch_id', 'state')
def _check_batch_id_required(self):
    for rec in self:
        if rec.state != 'draft' and not rec.batch_id:
            raise ValidationError(_(
                "Un dossier de dépôt est obligatoire dès la confirmation de la réparation."
            ))
```

- [ ] **Step 1.7: Run test again, expect PASS**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestDeferredBatchCreation.test_create_repair_without_batch
```

Expected: PASS.

- [ ] **Step 1.8: Write failing test for "action_confirm auto-wraps batch"**

Add:

```python
def test_confirm_creates_batch_when_missing(self):
    repair = self._new_draft_repair()
    self.assertFalse(repair.batch_id)
    repair.action_confirm()
    self.assertTrue(repair.batch_id, "action_confirm must populate batch_id")
    self.assertEqual(repair.batch_id.partner_id, self.partner)
    self.assertEqual(repair.state, 'confirmed')

def test_confirm_keeps_existing_batch(self):
    existing = self.Batch.create({'partner_id': self.partner.id})
    repair = self._new_draft_repair(batch_id=existing.id)
    repair.action_confirm()
    self.assertEqual(repair.batch_id, existing)

def test_confirm_requires_partner(self):
    repair = self.Repair.create({'product_tmpl_id': self.product_tmpl.id})
    with self.assertRaises(UserError):
        repair.action_confirm()
```

- [ ] **Step 1.9: Run tests, expect FAIL on the first two (UserError test may pass by accident)**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestDeferredBatchCreation
```

Expected: `test_confirm_creates_batch_when_missing` fails with `ValidationError` because `super().action_confirm()` runs the state transition without a batch, hitting the constraint.

- [ ] **Step 1.10: Add batch auto-wrap inside `action_confirm`**

Locate `def action_confirm` in `repair_custom/models/repair_order.py` (use `grep -n "def action_confirm" repair_custom/models/repair_order.py`). At the very top of the method body (before the existing logic), insert:

```python
Batch = self.env['repair.batch']
for rec in self:
    if not rec.partner_id:
        raise UserError(_("Veuillez renseigner un client avant de confirmer la réparation."))
    if not rec.batch_id:
        rec.batch_id = Batch.create({
            'partner_id': rec.partner_id.id,
            'date': rec.entry_date or fields.Datetime.now(),
            'company_id': rec.company_id.id,
        })
```

If `fields` or `UserError` are not already imported at the top of the file, verify the existing imports (Odoo repair_order.py already imports both — confirm with `head -10 repair_custom/models/repair_order.py`; add if missing).

- [ ] **Step 1.11: Run tests, expect PASS**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestDeferredBatchCreation
```

Expected: all three tests pass.

- [ ] **Step 1.12: Write test for `action_add_device_to_batch` unchanged**

Add:

```python
def test_action_add_device_to_batch_unchanged(self):
    # Confirm the first repair so the batch exists
    r1 = self._new_draft_repair()
    r1.action_confirm()
    batch = r1.batch_id
    self.assertTrue(batch)

    # Simulate the existing add-device flow: create a sibling draft that
    # points at the same batch explicitly (mirrors the wizard behavior).
    r2 = self.Repair.create({
        'partner_id': self.partner.id,
        'product_tmpl_id': self.product_tmpl.id,
        'batch_id': batch.id,
    })
    self.assertEqual(r2.batch_id, batch)
```

- [ ] **Step 1.13: Run tests, expect PASS (no code change needed)**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestDeferredBatchCreation
```

Expected: PASS — explicit `batch_id` on create is still honored.

- [ ] **Step 1.14: Commit**

```bash
git add repair_custom/models/repair_order.py repair_custom/tests/__init__.py repair_custom/tests/test_batch_ux_polish.py
git commit -m "$(cat <<'EOF'
repair_custom: defer batch creation from create() to action_confirm

Draft repairs no longer auto-wrap a singleton batch. The batch is
created (or the existing one reused) inside action_confirm, enforcing
the invariant where it actually matters: every non-draft repair has a
batch. Unblocks editing of repair fields while the user is still
filling in the initial info.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Archive cascade on unlink/write

**Files:**
- Modify: `repair_custom/models/repair_order.py` (add `unlink` + extend `write`)
- Test: `repair_custom/tests/test_batch_ux_polish.py`

- [ ] **Step 2.1: Write failing test "unlink last repair archives batch"**

Add to `test_batch_ux_polish.py`:

```python
@tagged('-at_install', 'post_install', 'repair_custom')
class TestArchiveCascade(RepairBatchUxCommon):
    def _confirmed(self, **overrides):
        r = self._new_draft_repair(**overrides)
        r.action_confirm()
        return r

    def test_unlink_last_repair_archives_batch(self):
        repair = self._confirmed()
        batch = repair.batch_id
        repair.unlink()
        self.assertFalse(batch.active, "Batch must be archived after last repair deleted")

    def test_unlink_with_siblings_keeps_batch_active(self):
        r1 = self._confirmed()
        batch = r1.batch_id
        r2 = self.Repair.create({
            'partner_id': self.partner.id,
            'product_tmpl_id': self.product_tmpl.id,
            'batch_id': batch.id,
        })
        r2.action_confirm()
        r1.unlink()
        self.assertTrue(batch.active)
```

- [ ] **Step 2.2: Run tests, expect FAIL**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestArchiveCascade
```

Expected: FAIL — batch remains active after unlinking the only repair.

- [ ] **Step 2.3: Add `unlink` override**

In `repair_custom/models/repair_order.py`, add the method near the other CRUD overrides (near `create`):

```python
def unlink(self):
    batches = self.mapped('batch_id')
    res = super().unlink()
    for batch in batches.exists():
        if not batch.repair_ids.filtered('active'):
            batch.active = False
    return res
```

- [ ] **Step 2.4: Run tests, expect PASS**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestArchiveCascade
```

Expected: both tests pass.

- [ ] **Step 2.5: Write tests for archive/un-archive symmetry**

Add:

```python
def test_archive_last_active_repair_archives_batch(self):
    repair = self._confirmed()
    batch = repair.batch_id
    repair.active = False
    self.assertFalse(batch.active)

def test_unarchive_repair_unarchives_batch(self):
    repair = self._confirmed()
    batch = repair.batch_id
    repair.active = False
    self.assertFalse(batch.active)
    repair.active = True
    self.assertTrue(batch.active)

def test_archive_with_active_siblings_keeps_batch_active(self):
    r1 = self._confirmed()
    batch = r1.batch_id
    r2 = self.Repair.create({
        'partner_id': self.partner.id,
        'product_tmpl_id': self.product_tmpl.id,
        'batch_id': batch.id,
    })
    r2.action_confirm()
    r1.active = False
    self.assertTrue(batch.active, "Batch stays active while any sibling is active")
```

- [ ] **Step 2.6: Run tests, expect FAIL**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestArchiveCascade
```

Expected: the archive symmetry tests fail; no cascade on active flip yet.

- [ ] **Step 2.7: Add/extend `write` override**

Check if `repair.order` already overrides `write` (grep in file). If it does, extend the existing method with the block below; otherwise add a new override near `unlink`:

```python
def write(self, vals):
    res = super().write(vals)
    if 'active' in vals:
        for batch in self.mapped('batch_id').exists():
            active_children = batch.repair_ids.filtered('active')
            if vals['active'] is False and not active_children and batch.active:
                batch.active = False
            elif vals['active'] is True and active_children and not batch.active:
                batch.active = True
    return res
```

If the file already has a `write` override, the integration point is the same — add the `if 'active' in vals:` block right before the existing `return res`.

Note: `batch.repair_ids` is an O2M that by default excludes archived children because Odoo's `search` applies `active=True`. To be explicit and robust, replace `batch.repair_ids.filtered('active')` with an explicit read using `.with_context(active_test=False)`:

```python
def write(self, vals):
    res = super().write(vals)
    if 'active' in vals:
        batches = self.mapped('batch_id').exists()
        for batch in batches:
            all_children = batch.with_context(active_test=False).repair_ids
            active_children = all_children.filtered('active')
            if vals['active'] is False and not active_children and batch.active:
                batch.active = False
            elif vals['active'] is True and active_children and not batch.active:
                batch.active = True
    return res
```

Use this explicit form.

- [ ] **Step 2.8: Run tests, expect PASS**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestArchiveCascade
```

Expected: all five archive-cascade tests pass.

- [ ] **Step 2.9: Commit**

```bash
git add repair_custom/models/repair_order.py repair_custom/tests/test_batch_ux_polish.py
git commit -m "$(cat <<'EOF'
repair_custom: archive-cascade batch when last repair is removed

repair.order.unlink() archives the parent batch when the last active
repair is gone. repair.order.write() handles the same symmetry when
repairs are archived/un-archived via active flag. Preserves batch's
FK references (sale orders, invoices, appointments, chatter) instead
of deleting them.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Aggregated `delivery_state` on `repair.batch`

**Files:**
- Modify: `repair_custom/models/repair_batch.py`
- Test: `repair_custom/tests/test_batch_ux_polish.py`

- [ ] **Step 3.1: Write failing tests for the four `delivery_state` outcomes**

Add to `test_batch_ux_polish.py`:

```python
@tagged('-at_install', 'post_install', 'repair_custom')
class TestBatchDeliveryState(RepairBatchUxCommon):
    def _confirmed(self, **overrides):
        r = self._new_draft_repair(**overrides)
        r.action_confirm()
        return r

    def _make_batch_with_repairs(self, n=2):
        r1 = self._confirmed()
        batch = r1.batch_id
        repairs = r1
        for _ in range(n - 1):
            r = self.Repair.create({
                'partner_id': self.partner.id,
                'product_tmpl_id': self.product_tmpl.id,
                'batch_id': batch.id,
            })
            r.action_confirm()
            repairs |= r
        return batch, repairs

    def test_delivery_state_none_default(self):
        batch, _ = self._make_batch_with_repairs(2)
        self.assertEqual(batch.delivery_state, 'none')

    def test_delivery_state_all_delivered(self):
        batch, repairs = self._make_batch_with_repairs(2)
        repairs.write({'delivery_state': 'delivered'})
        self.assertEqual(batch.delivery_state, 'delivered')

    def test_delivery_state_partial(self):
        batch, repairs = self._make_batch_with_repairs(2)
        repairs[0].delivery_state = 'delivered'
        self.assertEqual(batch.delivery_state, 'partial')

    def test_delivery_state_all_abandoned(self):
        batch, repairs = self._make_batch_with_repairs(2)
        repairs.write({'delivery_state': 'abandoned'})
        self.assertEqual(batch.delivery_state, 'abandoned')

    def test_delivery_state_ignores_abandoned_for_delivered_check(self):
        batch, repairs = self._make_batch_with_repairs(2)
        repairs[0].delivery_state = 'abandoned'
        repairs[1].delivery_state = 'delivered'
        # Eligible set (non-abandoned) is fully delivered → delivered
        self.assertEqual(batch.delivery_state, 'delivered')
```

- [ ] **Step 3.2: Run tests, expect FAIL**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestBatchDeliveryState
```

Expected: FAIL — `delivery_state` does not exist on `repair.batch`.

- [ ] **Step 3.3: Add the field + compute on `repair.batch`**

In `repair_custom/models/repair_batch.py`, locate the other `fields.Selection` declarations and add:

```python
delivery_state = fields.Selection(
    [
        ('none', "Aucune livraison"),
        ('partial', "Partiellement livré"),
        ('delivered', "Livré"),
        ('abandoned', "Abandonné"),
    ],
    string="État livraison",
    compute='_compute_delivery_state',
    store=True,
    default='none',
)

@api.depends('repair_ids.delivery_state', 'repair_ids.state')
def _compute_delivery_state(self):
    for batch in self:
        repairs = batch.repair_ids
        if not repairs:
            batch.delivery_state = 'none'
            continue
        eligible = repairs.filtered(lambda r: r.delivery_state != 'abandoned')
        if not eligible:
            batch.delivery_state = 'abandoned'
            continue
        delivered = eligible.filtered(lambda r: r.delivery_state == 'delivered')
        if len(delivered) == len(eligible):
            batch.delivery_state = 'delivered'
        elif delivered:
            batch.delivery_state = 'partial'
        else:
            batch.delivery_state = 'none'
```

Ensure `api` is imported at the top of the file (`from odoo import api, fields, models, _` — check and add if missing).

- [ ] **Step 3.4: Run tests, expect PASS**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestBatchDeliveryState
```

Expected: all five tests pass.

- [ ] **Step 3.5: Commit**

```bash
git add repair_custom/models/repair_batch.py repair_custom/tests/test_batch_ux_polish.py
git commit -m "$(cat <<'EOF'
repair_custom: add aggregated delivery_state on repair.batch

Stored compute with four values (none/partial/delivered/abandoned)
aggregated from repair_ids.delivery_state. Powers the batch form
badge and tree-view decoration.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Batch `Livrer` button + tree decorations + form badge

**Files:**
- Modify: `repair_custom/views/repair_batch_views.xml`
- Modify: `repair_custom/views/repair_views.xml`
- Test: `repair_custom/tests/test_batch_ux_polish.py`

- [ ] **Step 4.1: Write failing test for `Livrer` button wiring**

The existing `batch.action_mark_delivered` was delivered by sub-project 3. We test that the *predicate* used for the button correctly refuses when no eligible repair exists, and that the action transitions eligible repairs.

Add to `test_batch_ux_polish.py`:

```python
@tagged('-at_install', 'post_install', 'repair_custom')
class TestBatchLivrerButton(RepairBatchUxCommon):
    def _done_repair(self, batch=None):
        r = self._new_draft_repair(batch_id=batch.id if batch else False)
        r.action_confirm()
        # Simulate the technician path: we only need state='done' for the
        # delivery predicate. If action_repair_done has heavy preconditions
        # in this codebase, write the state directly — the test covers the
        # Livrer button, not the done transition.
        r.write({'state': 'done'})
        return r

    def test_batch_livrer_no_eligible_raises(self):
        batch = self.Batch.create({'partner_id': self.partner.id})
        with self.assertRaises(UserError):
            batch.action_mark_delivered()

    def test_batch_livrer_bulk_transitions(self):
        r1 = self._done_repair()
        batch = r1.batch_id
        r2 = self._done_repair(batch=batch)
        batch.action_mark_delivered()
        self.assertEqual(r1.delivery_state, 'delivered')
        self.assertEqual(r2.delivery_state, 'delivered')
        self.assertEqual(batch.delivery_state, 'delivered')
```

Note: if writing `state='done'` triggers heavy side effects in this codebase (SAR, pickings) that fail in a minimal test env, replace `r.write({'state': 'done'})` with `r.with_context(skip_pickup_notify_prompt=True, skip_repair_pickup_transition=True).action_repair_done()` — both context keys come from sub-project 3 and are already in place.

- [ ] **Step 4.2: Run tests, expect PASS on `no_eligible_raises`, possibly PASS on `bulk_transitions`**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestBatchLivrerButton
```

Expected: both tests PASS since `action_mark_delivered` already exists from sub-project 3. If `bulk_transitions` fails because the test fixture doesn't meet the eligibility predicate, adjust the fixture (e.g., ensure `delivery_state == 'none'` post state-write). These tests are guardrails against regression — no implementation change needed for them to pass.

- [ ] **Step 4.3: Add `Livrer` button on the batch form header**

In `repair_custom/views/repair_batch_views.xml`, find the `<header>` of the batch form view (use `grep -n "<header>" repair_custom/views/repair_batch_views.xml`). Add:

```xml
<button name="action_mark_delivered"
        type="object"
        string="Livrer"
        class="btn-primary"
        invisible="delivery_state in ('delivered', 'abandoned')"/>
```

- [ ] **Step 4.4: Add `delivery_state` badge in batch form body and column in tree view**

Still in `repair_custom/views/repair_batch_views.xml`:

In the form body (near the other status display), add:

```xml
<field name="delivery_state" widget="badge"
       decoration-success="delivery_state == 'delivered'"
       decoration-info="delivery_state == 'partial'"
       decoration-muted="delivery_state in ('none', 'abandoned')"/>
```

In the tree view root `<tree ...>` tag, add `decoration-success="delivery_state == 'delivered'"` to the existing decoration attributes (preserve the others). Inside the tree, add:

```xml
<field name="delivery_state"/>
```

- [ ] **Step 4.5: Add `decoration-success` to repair tree view**

In `repair_custom/views/repair_views.xml`, find the `view_repair_order_tree` record (and any other `<tree>` that renders repair rows — e.g., atelier tree). To the root `<tree>` tag add:

```xml
decoration-success="delivery_state == 'delivered'"
```

Preserve all existing decoration attributes on the same tag. The field `delivery_state` is already defined on `repair.order` (line 181) and is visible/optional in the existing tree — if it's not listed as a `<field>` in the tree, add `<field name="delivery_state" optional="show"/>`.

- [ ] **Step 4.6: Upgrade the module and verify visually**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

Then start Odoo interactively, open the batch form for a multi-device batch, verify the `Livrer` button and badge render. Open the batch tree view and a delivered row — verify green row. Open the repair tree view — same green decoration on delivered rows.

- [ ] **Step 4.7: Commit**

```bash
git add repair_custom/views/repair_batch_views.xml repair_custom/views/repair_views.xml repair_custom/tests/test_batch_ux_polish.py
git commit -m "$(cat <<'EOF'
repair_custom: Livrer button + delivery_state badge/column/decorations

Batch form gets a Livrer header button wired to the existing
batch.action_mark_delivered. Adds a delivery_state badge in the form
body, a column on the batch tree view with decoration-success, and
the same decoration-success on the repair tree view for visual
consistency.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Sibling-repair banner on the repair form

**Files:**
- Modify: `repair_custom/models/repair_order.py` (add computed fields)
- Modify: `repair_custom/views/repair_views.xml` (add banner)
- Test: `repair_custom/tests/test_batch_ux_polish.py`

- [ ] **Step 5.1: Write failing tests for `sibling_repair_ids` / `has_siblings`**

Add to `test_batch_ux_polish.py`:

```python
@tagged('-at_install', 'post_install', 'repair_custom')
class TestSiblingBanner(RepairBatchUxCommon):
    def _confirmed(self, batch=None):
        r = self._new_draft_repair(batch_id=batch.id if batch else False)
        r.action_confirm()
        return r

    def test_has_siblings_false_for_singleton(self):
        repair = self._confirmed()
        self.assertFalse(repair.has_siblings)
        self.assertFalse(repair.sibling_repair_ids)

    def test_has_siblings_true_when_batch_has_peers(self):
        r1 = self._confirmed()
        r2 = self._confirmed(batch=r1.batch_id)
        self.assertTrue(r1.has_siblings)
        self.assertTrue(r2.has_siblings)

    def test_sibling_list_excludes_self(self):
        r1 = self._confirmed()
        r2 = self._confirmed(batch=r1.batch_id)
        self.assertNotIn(r1, r1.sibling_repair_ids)
        self.assertIn(r2, r1.sibling_repair_ids)
        self.assertNotIn(r2, r2.sibling_repair_ids)
        self.assertIn(r1, r2.sibling_repair_ids)
```

- [ ] **Step 5.2: Run tests, expect FAIL**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestSiblingBanner
```

Expected: FAIL — fields don't exist.

- [ ] **Step 5.3: Add `sibling_repair_ids` and `has_siblings` on `repair.order`**

In `repair_custom/models/repair_order.py`, near the other computed fields (search for existing `compute=` lines in the model class; add near `batch_id` around line 492):

```python
sibling_repair_ids = fields.Many2many(
    'repair.order',
    string="Autres réparations du dossier",
    compute='_compute_sibling_repair_ids',
)
has_siblings = fields.Boolean(
    compute='_compute_sibling_repair_ids',
)

@api.depends('batch_id', 'batch_id.repair_ids')
def _compute_sibling_repair_ids(self):
    for rec in self:
        if not rec.batch_id:
            rec.sibling_repair_ids = False
            rec.has_siblings = False
            continue
        peers = rec.batch_id.repair_ids - rec
        rec.sibling_repair_ids = peers
        rec.has_siblings = bool(peers)
```

Note the choice: `Many2many` (not `One2many`) because there is no inverse FK pointing "back" from a sibling set — it's a virtual peer relation. `Many2many` with `compute=` and no relation table is valid in Odoo 17.

- [ ] **Step 5.4: Run tests, expect PASS**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestSiblingBanner
```

Expected: three tests pass.

- [ ] **Step 5.5: Add the banner to the repair form view**

In `repair_custom/views/repair_views.xml`, find `view_repair_order_form` (the main repair form — use `grep -n "view_repair_order_form\|<form " repair_custom/views/repair_views.xml | head`). Inside the `<sheet>` and above the `<notebook>` (if any) or at the top of the main form body, insert:

```xml
<div class="o_repair_siblings alert alert-info py-2 px-3 mb-2"
     role="alert"
     invisible="not has_siblings">
    <strong>Autres appareils du dossier :</strong>
    <field name="sibling_repair_ids"
           widget="many2many_tags"
           readonly="1"
           options="{'no_create': True, 'no_open': False}"/>
</div>
```

`many2many_tags` renders each sibling as a clickable chip labeled by `display_name`. It's the native Odoo widget with the closest UX match to the spec's "chip row" without custom JS. Clicking a tag opens the sibling record.

Also hide the banner on the atelier form if a separate form exists for the workshop UI (check `view_repair_order_atelier_form`). If so, apply the same `<div>` block in that form too — techs benefit most from sibling awareness.

- [ ] **Step 5.6: Upgrade and verify**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

Open a multi-device batch, open one repair — verify banner appears with sibling chips. Open a singleton-batch repair — verify banner is hidden.

- [ ] **Step 5.7: Commit**

```bash
git add repair_custom/models/repair_order.py repair_custom/views/repair_views.xml repair_custom/tests/test_batch_ux_polish.py
git commit -m "$(cat <<'EOF'
repair_custom: sibling-repair banner on the repair form

Adds sibling_repair_ids + has_siblings computed fields on repair.order.
Renders a compact alert-info banner above the notebook listing peer
repairs of the same batch as many2many_tags chips. Hidden when the
batch has no siblings. Gives technicians passive awareness of deposit
context without switching views.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Manager navigation bridge

**Files:**
- Modify: `repair_custom/models/repair_order.py` (sibling-count compute, `action_open_batch`)
- Modify: `repair_custom/views/repair_views.xml` (smart button, tree column, group-by filter)
- Modify: `repair_custom/views/repair_batch_views.xml` (default `repair_count > 1` filter)
- Test: `repair_custom/tests/test_batch_ux_polish.py`

- [ ] **Step 6.1: Write failing test for `batch_sibling_count` and `action_open_batch`**

Add:

```python
@tagged('-at_install', 'post_install', 'repair_custom')
class TestNavigationBridge(RepairBatchUxCommon):
    def _confirmed(self, batch=None):
        r = self._new_draft_repair(batch_id=batch.id if batch else False)
        r.action_confirm()
        return r

    def test_batch_sibling_count_matches_repair_count(self):
        r1 = self._confirmed()
        self.assertEqual(r1.batch_sibling_count, 1)
        r2 = self._confirmed(batch=r1.batch_id)
        # invalidate cached compute on r1
        r1.invalidate_recordset(['batch_sibling_count'])
        self.assertEqual(r1.batch_sibling_count, 2)
        self.assertEqual(r2.batch_sibling_count, 2)

    def test_batch_sibling_count_zero_for_batchless(self):
        draft = self._new_draft_repair()
        self.assertFalse(draft.batch_id)
        self.assertEqual(draft.batch_sibling_count, 0)

    def test_action_open_batch_returns_form_action(self):
        r = self._confirmed()
        action = r.action_open_batch()
        self.assertEqual(action['res_model'], 'repair.batch')
        self.assertEqual(action['res_id'], r.batch_id.id)
        self.assertEqual(action['view_mode'], 'form')
```

- [ ] **Step 6.2: Run tests, expect FAIL**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestNavigationBridge
```

Expected: FAIL — `batch_sibling_count` and `action_open_batch` don't exist.

- [ ] **Step 6.3: Add `batch_sibling_count` + `action_open_batch` on `repair.order`**

In `repair_custom/models/repair_order.py`, near the sibling fields added in Task 5:

```python
batch_sibling_count = fields.Integer(
    compute='_compute_batch_sibling_count',
    string="Réparations dans le dossier",
)

@api.depends('batch_id.repair_ids')
def _compute_batch_sibling_count(self):
    for rec in self:
        rec.batch_sibling_count = len(rec.batch_id.repair_ids) if rec.batch_id else 0

def action_open_batch(self):
    self.ensure_one()
    if not self.batch_id:
        raise UserError(_("Cette réparation n'est pas liée à un dossier."))
    return {
        'type': 'ir.actions.act_window',
        'res_model': 'repair.batch',
        'res_id': self.batch_id.id,
        'view_mode': 'form',
        'target': 'current',
    }
```

Note on the existing form: lines 278-279 already reference `batch_count` as a smart-button-like integer. Keep that field alone (it may have other semantics — don't rename). The new `batch_sibling_count` is purpose-built for the smart button.

- [ ] **Step 6.4: Run tests, expect PASS**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom:TestNavigationBridge
```

Expected: PASS.

- [ ] **Step 6.5: Add the smart button on the repair form**

In `repair_custom/views/repair_views.xml`, find the stat button area of `view_repair_order_form` (usually inside a `<div class="oe_button_box" name="button_box">` — use `grep -n "oe_button_box\|button_box" repair_custom/views/repair_views.xml`). Add:

```xml
<button name="action_open_batch"
        type="object"
        class="oe_stat_button"
        icon="fa-folder-open"
        invisible="not batch_id">
    <field name="batch_sibling_count" widget="statinfo" string="Dossier"/>
</button>
```

If there's already a batch-related smart button or inline display (line 278-279 uses `batch_count`), leave it and add this one adjacent — they cover different surfaces.

- [ ] **Step 6.6: Add `batch_id` column on the repair tree view**

Still in `repair_custom/views/repair_views.xml`, find `view_repair_order_tree` and confirm line 129 already has `<field name="batch_id" optional="hide"/>`. Change `optional="hide"` to `optional="show"` so the column is visible by default.

- [ ] **Step 6.7: Add "Grouper par dossier" filter to the repair search view**

In `repair_custom/views/repair_views.xml`, find the repair search view (grep for `search_view_id\|<search `). Inside the `<group expand="0" string="Group By">` section (or create one if missing), add:

```xml
<filter name="group_by_batch"
        string="Dossier"
        context="{'group_by': 'batch_id'}"/>
```

- [ ] **Step 6.8: Add the `multi_device_batches` default filter to the batch tree search view**

In `repair_custom/views/repair_batch_views.xml`, find the batch search view. Inside `<search>`, add:

```xml
<filter name="multi_device_batches"
        string="Dossiers multi-appareils"
        domain="[('repair_count', '>', 1)]"/>
```

Then find the batch window action (`ir.actions.act_window` that lists batches — grep for `repair.batch` in the same file, the `res_model` tag) and set / extend `context`:

```xml
<field name="context">{'search_default_multi_device_batches': 1}</field>
```

If the action already has a `context` field, merge the key instead of overwriting other keys.

- [ ] **Step 6.9: Upgrade and verify**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

In the UI:
- Open a multi-device repair → verify smart button "Dossier (2)" appears and opens the batch form.
- Open the repair tree → verify `Dossier` column is visible by default.
- In the repair search view, toggle "Dossier" group-by → repairs group under their batch.
- Open the batch tree → singleton batches are hidden by default; toggle the filter off to see them.

- [ ] **Step 6.10: Commit**

```bash
git add repair_custom/models/repair_order.py repair_custom/views/repair_views.xml repair_custom/views/repair_batch_views.xml repair_custom/tests/test_batch_ux_polish.py
git commit -m "$(cat <<'EOF'
repair_custom: navigation bridge between repair.order and repair.batch

Adds a smart button with sibling count on the repair form, promotes
the batch_id column on the repair tree to optional=show, adds a
Grouper par dossier filter in the repair search view, and sets the
batch tree to default-filter on repair_count > 1 so singletons stay
out of the way.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Manifest version bump + full test run

**Files:**
- Modify: `repair_custom/__manifest__.py`

- [ ] **Step 7.1: Bump the manifest version**

In `repair_custom/__manifest__.py`, change:

```python
'version': '17.0.1.5.0',
```

to:

```python
'version': '17.0.1.6.0',
```

- [ ] **Step 7.2: Run the full module test suite**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom
```

Expected: all repair_custom tests pass (including existing sub-project 1/2/3 tests — regression check).

- [ ] **Step 7.3: If the existing test suite has regressions, diagnose**

Common regression spots introduced by this plan:
- Sub-project 3's `test_create_repair_auto_wraps_batch` — this test **must** be removed or updated, because the behavior it asserts was reverted in Task 1. Expected resolution: open `repair_custom/tests/test_completion_pickup.py`, find `test_create_repair_auto_wraps_batch`, and update it to assert the new behavior (draft repair has no batch; batch appears after `action_confirm`). Similarly for `test_pre_migration_wraps_batchless_repairs` — keep it (historical data invariant still holds).
- Any constraint or view attrs that assumed `batch_id` is always populated — grep: `grep -rn "batch_id" repair_custom/models repair_custom/views | grep -v test`.

Fix any failing tests inline.

- [ ] **Step 7.4: Commit manifest bump + any regression fixes together**

```bash
git add repair_custom/__manifest__.py
# If regressions required edits:
# git add <any_other_changed_files>
git commit -m "$(cat <<'EOF'
repair_custom: bump version to 17.0.1.6.0 for batch/repair UX polish

Covers the five polish items from the 2026-04-22 design spec:
deferred batch creation, aggregated batch delivery_state + Livrer
button, sibling-repair banner, archive cascade, navigation bridge.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] **Step F.1: Re-run the full suite one more time**

```bash
./odoo-bin -c ../odoo.conf -u repair_custom --test-enable --stop-after-init \
    --test-tags=/repair_custom
```

Expected: green.

- [ ] **Step F.2: Manual QA walkthrough**

Follow the checklist from the spec (section "Manual QA checklist", items 1–12). Each item verifies a distinct behavior. Report any deviations.

- [ ] **Step F.3: Handoff**

Branch `feature/repair-batch-ux-polish` is ready to merge. Use `superpowers:finishing-a-development-branch` to decide between direct merge and PR. The deferred Theme A bullets are at the end of the spec for the next brainstorm session.
