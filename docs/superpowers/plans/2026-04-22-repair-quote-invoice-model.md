# Repair Quote & Invoice Model Rework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Invert the "one batch = one sale.order" model so every repair carries its own quote, consolidate batch invoicing into one account.move with per-device section headers, hide/replace the native sale.order invoice button on repair quotes, auto-stamp repair metadata defensively on any account.move born from a repair quote, and extend batch pickup to cover partial-acceptance scenarios (refused-quote devices picked up un-repaired).

**Architecture:** No new models. `repair.pricing.wizard` is trimmed to quote-only (invoice mode and batch-mode walkthrough deleted). A new `repair.batch._invoice_approved_quotes(repairs)` core helper is the single code path for invoicing — reached via three surfaces (repair-form button, batch-form button, replaced sale.order button). Consolidation uses native `sale.order._create_invoices()` plus a post-process that injects `display_type='line_section'` headers per source SO. An `account.move.create()` override auto-stamps `repair_id` / `batch_id` from the sale-line fallback. Batch `action_mark_delivered` is extended to accept refused-quote repairs and silently set their `state='cancel'`.

**Tech Stack:** Odoo 17 (Python 3.10+, PostgreSQL), XML views, Odoo test framework (`TransactionCase`). No new Python deps.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `repair_custom/__manifest__.py` | Module metadata | Modify — version `17.0.1.6.0` → `17.0.1.7.0`, register new `views/sale_order_views.xml` |
| `repair_custom/models/repair_extensions.py` | `account.move` / `sale.order` extensions | Modify — add `AccountMove.create()` override for auto-stamp; add `SaleOrder.action_invoice_repair_quote` |
| `repair_custom/models/repair_batch.py` | Batch behavior | Modify — add `has_invoiceable_quotes`, `action_invoice_approved_quotes`, `_invoice_approved_quotes`, `_inject_repair_section_headers`; extend `action_mark_delivered` |
| `repair_custom/models/repair_order.py` | Repair behavior | Modify — add `is_quote_invoiceable`, `action_invoice_repair_quote`; delete `action_open_pricing_wizard` |
| `repair_custom/wizard/repair_pricing_wizard.py` | Quote creation wizard | Modify (heavy trim) — drop `generation_type`, `batch_id`, `remaining_repair_ids`, `accumulated_lines_json`, `step_info`, `action_next_step`, `_create_global_invoice`; rename `_create_global_sale_order` → `_create_quote` |
| `repair_custom/views/repair_pricing_wizard_views.xml` | Wizard form | Modify — drop batch/invoice-mode XML elements |
| `repair_custom/views/repair_views.xml` | Repair form + tree + search | Modify — relabel "Devis/Facture" → "Devis", add "Facturer le devis" button with `is_quote_invoiceable` gate, change wizard button visibility rule |
| `repair_custom/views/repair_batch_views.xml` | Batch form | Modify — add "Facturer les devis acceptés" header button |
| `repair_custom/views/sale_order_views.xml` | Sale order form | **Create** — hide native `action_create_invoice` when `computed_order_type == 'repair_quote'`, add replacement button |
| `repair_custom/tests/test_quote_invoice_model.py` | Theme A tests | **Create** — 5 classes covering all Theme A behaviors |
| `repair_custom/tests/test_quote_lifecycle.py` | Sub-project 2 tests | Modify — audit & rewrite any grouped-SO assertions |

---

## Task 1: Manifest bump + register new view file

**Files:**
- Modify: `repair_custom/__manifest__.py`

- [ ] **Step 1: Bump version and register the new view file**

Read `repair_custom/__manifest__.py` first to locate the exact lines.

Change `'version': '17.0.1.6.0'` to `'version': '17.0.1.7.0'`.

In the `'data': [...]` list, add the new line:

```python
'views/sale_order_views.xml',
```

Add it adjacent to the other view file entries (order doesn't matter for loading, but keep alphabetical next to `repair_*` entries for cleanliness).

- [ ] **Step 2: Verify module still loads**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --log-level=warn 2>&1 | tail -20`

Expected: no errors referencing the manifest or missing view file. The `sale_order_views.xml` file doesn't exist yet, so Odoo will fail to load it. Create an empty placeholder to unblock:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Filled in Task 8 -->
</odoo>
```

Save as `repair_custom/views/sale_order_views.xml`.

Re-run the upgrade command. Expected: clean exit, no errors.

- [ ] **Step 3: Commit**

```bash
git add repair_custom/__manifest__.py repair_custom/views/sale_order_views.xml
git commit -m "repair_custom: bump to 17.0.1.7.0 + register sale_order_views.xml"
```

---

## Task 2: `account.move` auto-stamp on create

**Files:**
- Modify: `repair_custom/models/repair_extensions.py` (AccountMove class, ~line 181-245)
- Test: `repair_custom/tests/test_quote_invoice_model.py` (new)

- [ ] **Step 1: Create the test file skeleton**

Create `repair_custom/tests/test_quote_invoice_model.py`:

```python
# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from .common import RepairQuoteCase


class TestAccountMoveAutoStamp(RepairQuoteCase):
    """account.move.create auto-stamps repair_id/batch_id via sale-line fallback."""

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()
        self.so = self._make_sale_order_linked(self.repair)
        self.so.action_confirm()  # state='sale' so _create_invoices() works
```

- [ ] **Step 2: Register the new test file**

Read `repair_custom/tests/__init__.py`. Add:

```python
from . import test_quote_invoice_model
```

after the existing `from . import test_quote_lifecycle` line.

- [ ] **Step 3: Write the failing auto-stamp test**

Append to `test_quote_invoice_model.py` inside `TestAccountMoveAutoStamp`:

```python
    def test_auto_stamp_on_native_create_invoices(self):
        """Calling sale.order._create_invoices() populates repair_id & batch_id."""
        moves = self.so._create_invoices()
        self.assertEqual(len(moves), 1)
        move = moves
        self.assertEqual(move.repair_id, self.repair,
                         "repair_id should auto-stamp when exactly one repair resolves")
        self.assertEqual(move.batch_id, self.repair.batch_id,
                         "batch_id should auto-stamp when exactly one batch resolves")

    def test_auto_stamp_noop_on_non_out_invoice(self):
        """move_type != 'out_invoice' does not trigger stamping."""
        move = self.env['account.move'].create({
            'move_type': 'out_refund',
            'partner_id': self.partner.id,
        })
        self.assertFalse(move.repair_id)
        self.assertFalse(move.batch_id)

    def test_auto_stamp_idempotent_when_prestamped(self):
        """Pre-existing repair_id is preserved (not overwritten)."""
        other_repair = self._make_repair()
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'repair_id': other_repair.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.service_product.id,
                'name': 'Test',
                'quantity': 1,
                'price_unit': 10.0,
            })],
        })
        self.assertEqual(move.repair_id, other_repair,
                         "Pre-stamped repair_id must survive the create hook")
```

- [ ] **Step 4: Run tests — verify they fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestAccountMoveAutoStamp 2>&1 | tail -40`

Expected: `test_auto_stamp_on_native_create_invoices` FAILS (assertion on `move.repair_id`). The other two may already pass (the code path doesn't touch them yet), but the key one must fail to confirm TDD.

- [ ] **Step 5: Implement the auto-stamp override**

In `repair_custom/models/repair_extensions.py`, locate the `AccountMove` class (inherits `account.move`, around line 181). Add inside the class, before the `_is_equipment_sale_invoice` method:

```python
    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._auto_stamp_repair_metadata()
        return moves

    def _auto_stamp_repair_metadata(self):
        """Defensively populate repair_id / batch_id on repair-linked invoices
        regardless of origin (our button, native sale.order button, list-view
        bulk, scripted creation)."""
        for move in self:
            if move.move_type != 'out_invoice':
                continue
            if move.repair_id and move.batch_id:
                continue
            repairs = move.invoice_line_ids.mapped(
                'sale_line_ids.order_id.repair_order_ids'
            )
            if not repairs:
                continue
            batches = repairs.mapped('batch_id')
            vals = {}
            if not move.batch_id and len(batches) == 1:
                vals['batch_id'] = batches.id
            if not move.repair_id and len(repairs) == 1:
                vals['repair_id'] = repairs.id
            if vals:
                move.write(vals)
```

- [ ] **Step 6: Run tests — verify they pass**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestAccountMoveAutoStamp 2>&1 | tail -20`

Expected: all three tests PASS.

- [ ] **Step 7: Commit**

```bash
git add repair_custom/models/repair_extensions.py repair_custom/tests/test_quote_invoice_model.py repair_custom/tests/__init__.py
git commit -m "repair_custom: auto-stamp repair_id/batch_id on account.move create"
```

---

## Task 3: Batch consolidation core helper — `_inject_repair_section_headers`

**Files:**
- Modify: `repair_custom/models/repair_batch.py`
- Test: `repair_custom/tests/test_quote_invoice_model.py`

- [ ] **Step 1: Write the failing section-header injection test**

Append to `test_quote_invoice_model.py`:

```python
class TestSectionHeaderInjection(RepairQuoteCase):
    """_inject_repair_section_headers prepends a line_section per source SO."""

    def setUp(self):
        super().setUp()
        # Two repairs in one batch, each with its own sale.order
        self.repair_a = self._make_repair(internal_notes='Diag A')
        self.repair_a.serial_number = 'SN-AAA'
        self.repair_b = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Diag B',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_a.batch_id.id,
            'serial_number': 'SN-BBB',
        })
        self.repair_b._action_repair_confirm()
        self.so_a = self._make_sale_order_linked(self.repair_a)
        self.so_b = self._make_sale_order_linked(self.repair_b)
        self.so_a.action_confirm()
        self.so_b.action_confirm()

    def test_injects_one_header_per_source_so(self):
        moves = (self.so_a + self.so_b)._create_invoices()
        # Native may produce one consolidated move for same partner
        self.assertEqual(len(moves), 1)
        move = moves
        self.repair_a.batch_id._inject_repair_section_headers(move)
        sections = move.invoice_line_ids.filtered(
            lambda l: l.display_type == 'line_section'
        )
        self.assertEqual(len(sections), 2,
                         "One section header per source sale.order")

    def test_header_label_contains_device_and_sn(self):
        moves = (self.so_a + self.so_b)._create_invoices()
        move = moves
        self.repair_a.batch_id._inject_repair_section_headers(move)
        labels = move.invoice_line_ids.filtered(
            lambda l: l.display_type == 'line_section'
        ).mapped('name')
        # repair_a.device_id_name may be empty in the test fixture; focus on SN
        self.assertTrue(any('SN-AAA' in lbl for lbl in labels),
                        "Header must include the serial number")
        self.assertTrue(any('SN-BBB' in lbl for lbl in labels),
                        "Header must include both serial numbers")
```

- [ ] **Step 2: Run — verify they fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestSectionHeaderInjection 2>&1 | tail -30`

Expected: both tests FAIL with `AttributeError: 'repair.batch' object has no attribute '_inject_repair_section_headers'`.

- [ ] **Step 3: Implement the helper**

In `repair_custom/models/repair_batch.py`, locate the `RepairBatch` class. Add at the end of the class (before any existing `@api.model_create_multi create`):

```python
    def _inject_repair_section_headers(self, move):
        """Insert a display_type='line_section' header before each source SO's
        lines on a consolidated invoice. Labels mirror today's wizard format.

        Legacy SOs (linked to N repairs) fall back to the SO name — forward
        decision: no post-migration split, handle gracefully at read time."""
        self.ensure_one()
        lines_by_so = {}
        for line in move.invoice_line_ids.sorted('sequence'):
            if line.display_type in ('line_section', 'line_note'):
                continue
            sos = line.sale_line_ids.mapped('order_id')
            if not sos:
                continue
            so = sos[:1]
            lines_by_so.setdefault(so.id, []).append(line)

        seq = 0
        AccountMoveLine = self.env['account.move.line']
        for so_id, lines in lines_by_so.items():
            so = self.env['sale.order'].browse(so_id)
            if len(so.repair_order_ids) == 1:
                repair = so.repair_order_ids
                label = _("Réparation : %s") % (repair.device_id_name or so.name)
                if repair.serial_number:
                    label += _(" (S/N: %s)") % repair.serial_number
            else:
                label = _("Devis : %s") % so.name

            seq += 1
            AccountMoveLine.create({
                'move_id': move.id,
                'display_type': 'line_section',
                'name': label,
                'sequence': seq,
            })
            for line in lines:
                seq += 1
                line.sequence = seq
```

Ensure `_` is imported at the top of the file (it likely already is; if not, add `from odoo import _` to the existing odoo import).

- [ ] **Step 4: Run — verify they pass**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestSectionHeaderInjection 2>&1 | tail -15`

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add repair_custom/models/repair_batch.py repair_custom/tests/test_quote_invoice_model.py
git commit -m "repair_custom: add _inject_repair_section_headers on repair.batch"
```

---

## Task 4: Batch consolidation — `_invoice_approved_quotes` core helper

**Files:**
- Modify: `repair_custom/models/repair_batch.py`
- Test: `repair_custom/tests/test_quote_invoice_model.py`

- [ ] **Step 1: Write the failing helper test**

Append to `test_quote_invoice_model.py`:

```python
class TestInvoiceApprovedQuotes(RepairQuoteCase):
    """Core consolidation helper used by all three button surfaces."""

    def setUp(self):
        super().setUp()
        self.repair_a = self._make_repair()
        self.repair_a.serial_number = 'SN-A'
        self.repair_b = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Diag B',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_a.batch_id.id,
            'serial_number': 'SN-B',
        })
        self.repair_b._action_repair_confirm()
        self.so_a = self._make_sale_order_linked(self.repair_a)
        self.so_b = self._make_sale_order_linked(self.repair_b)
        self.batch = self.repair_a.batch_id

    def test_raises_when_no_repairs_passed(self):
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.batch._invoice_approved_quotes(self.Repair)

    def test_raises_when_no_sale_orders_linked(self):
        from odoo.exceptions import UserError
        orphan = self._make_repair()
        with self.assertRaises(UserError):
            orphan.batch_id._invoice_approved_quotes(orphan)

    def test_creates_consolidated_invoice_with_section_headers(self):
        # Both SOs confirmed and approved (quote_state=approved)
        self.so_a.action_confirm()
        self.so_b.action_confirm()
        result = self.batch._invoice_approved_quotes(
            self.repair_a + self.repair_b
        )
        # Helper returns an act_window dict
        self.assertEqual(result['res_model'], 'account.move')
        move = self.env['account.move'].browse(result['res_id'])
        self.assertTrue(move.exists())
        self.assertEqual(move.batch_id, self.batch,
                         "Consolidated move stamps batch_id")
        sections = move.invoice_line_ids.filtered(
            lambda l: l.display_type == 'line_section'
        )
        self.assertEqual(len(sections), 2,
                         "One section header per source SO")

    def test_singleton_invoice_stamps_repair_id(self):
        self.so_a.action_confirm()
        result = self.batch._invoice_approved_quotes(self.repair_a)
        move = self.env['account.move'].browse(result['res_id'])
        self.assertEqual(move.repair_id, self.repair_a,
                         "Singleton invoice stamps repair_id (via auto-stamp)")
        self.assertEqual(move.batch_id, self.batch)
```

- [ ] **Step 2: Run — verify they fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestInvoiceApprovedQuotes 2>&1 | tail -30`

Expected: all four FAIL with `AttributeError: '_invoice_approved_quotes'`.

- [ ] **Step 3: Implement the helper**

In `repair_custom/models/repair_batch.py`, add inside `RepairBatch` class, next to `_inject_repair_section_headers`:

```python
    def _invoice_approved_quotes(self, repairs):
        """Core helper: consolidate sale.orders of `repairs` into one
        account.move with per-repair section headers. Shared by the repair-
        form button, the batch-form button, and the sale.order replacement
        button."""
        self.ensure_one()
        if not repairs:
            raise UserError(_("Aucune réparation sélectionnée."))
        sale_orders = repairs.mapped('sale_order_id')
        if not sale_orders:
            raise UserError(_("Aucun devis lié aux réparations sélectionnées."))

        moves = sale_orders._create_invoices()
        for move in moves:
            self._inject_repair_section_headers(move)
            if not move.batch_id:
                move.batch_id = self.id
            # repair_id auto-stamped via account.move.create override when unique

        if len(moves) == 1:
            return {
                'name': _("Facture Générée"),
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'res_id': moves.id,
                'view_mode': 'form',
            }
        return {
            'name': _("Factures Générées"),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', moves.ids)],
        }
```

Ensure `UserError` is imported at the top of the file (check existing imports; `from odoo.exceptions import UserError` should already be there).

- [ ] **Step 4: Run — verify they pass**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestInvoiceApprovedQuotes 2>&1 | tail -15`

Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add repair_custom/models/repair_batch.py repair_custom/tests/test_quote_invoice_model.py
git commit -m "repair_custom: add _invoice_approved_quotes batch helper"
```

---

## Task 5: `repair.order.is_quote_invoiceable` computed field

**Files:**
- Modify: `repair_custom/models/repair_order.py`
- Test: `repair_custom/tests/test_quote_invoice_model.py`

- [ ] **Step 1: Write the failing compute test**

Append to `test_quote_invoice_model.py`:

```python
class TestIsQuoteInvoiceable(RepairQuoteCase):
    """is_quote_invoiceable gates the repair-form 'Facturer le devis' button."""

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()

    def test_false_when_no_sale_order(self):
        self.assertFalse(self.repair.is_quote_invoiceable)

    def test_false_when_quote_state_not_approved(self):
        self._make_sale_order_linked(self.repair)
        self.repair.quote_state = 'sent'
        self.assertFalse(self.repair.is_quote_invoiceable)

    def test_true_when_approved_and_to_invoice(self):
        so = self._make_sale_order_linked(self.repair)
        so.action_confirm()  # state=sale → sync to quote_state=approved
        # After action_confirm, invoice_status transitions to 'to invoice'
        self.assertEqual(self.repair.quote_state, 'approved')
        self.assertIn(so.invoice_status, ('to invoice', 'upselling'))
        self.assertTrue(self.repair.is_quote_invoiceable)

    def test_false_after_invoice_generated(self):
        so = self._make_sale_order_linked(self.repair)
        so.action_confirm()
        so._create_invoices()
        self.assertEqual(so.invoice_status, 'invoiced')
        self.assertFalse(self.repair.is_quote_invoiceable)
```

- [ ] **Step 2: Run — verify they fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestIsQuoteInvoiceable 2>&1 | tail -30`

Expected: all four FAIL — `is_quote_invoiceable` field doesn't exist.

- [ ] **Step 3: Implement the field**

In `repair_custom/models/repair_order.py`, locate a sensible spot near the other quote-related fields (around the existing `sale_order_id` definition, ~line 960). Add:

```python
    is_quote_invoiceable = fields.Boolean(
        compute='_compute_is_quote_invoiceable',
        string="Devis facturable",
    )

    @api.depends('quote_state', 'sale_order_id.invoice_status')
    def _compute_is_quote_invoiceable(self):
        for rec in self:
            rec.is_quote_invoiceable = (
                rec.quote_state == 'approved'
                and bool(rec.sale_order_id)
                and rec.sale_order_id.invoice_status in ('to invoice', 'upselling')
            )
```

- [ ] **Step 4: Run — verify they pass**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestIsQuoteInvoiceable 2>&1 | tail -15`

Expected: all four PASS.

- [ ] **Step 5: Commit**

```bash
git add repair_custom/models/repair_order.py repair_custom/tests/test_quote_invoice_model.py
git commit -m "repair_custom: add is_quote_invoiceable compute on repair.order"
```

---

## Task 6: `repair.order.action_invoice_repair_quote` + form button

**Files:**
- Modify: `repair_custom/models/repair_order.py`
- Modify: `repair_custom/views/repair_views.xml`
- Test: `repair_custom/tests/test_quote_invoice_model.py`

- [ ] **Step 1: Write the failing test**

Append to `test_quote_invoice_model.py`:

```python
class TestRepairInvoiceAction(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()
        self.so = self._make_sale_order_linked(self.repair)
        self.so.action_confirm()

    def test_action_delegates_to_batch_helper(self):
        result = self.repair.action_invoice_repair_quote()
        self.assertEqual(result['res_model'], 'account.move')
        move = self.env['account.move'].browse(result['res_id'])
        self.assertEqual(move.repair_id, self.repair)
        self.assertEqual(move.batch_id, self.repair.batch_id)
```

- [ ] **Step 2: Run — verify it fails**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestRepairInvoiceAction 2>&1 | tail -15`

Expected: FAIL with `AttributeError: action_invoice_repair_quote`.

- [ ] **Step 3: Implement the action**

In `repair_custom/models/repair_order.py`, near the `is_quote_invoiceable` field added in Task 5, add:

```python
    def action_invoice_repair_quote(self):
        """Per-repair invoicing. Delegates to the batch helper with self as
        the singleton repair set."""
        self.ensure_one()
        if not self.batch_id:
            raise UserError(_("Cette réparation n'est rattachée à aucun dossier."))
        return self.batch_id._invoice_approved_quotes(self)
```

Verify `UserError` and `_` are already imported at the top of the file (they are — sub-project 2 uses them).

- [ ] **Step 4: Run — verify it passes**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestRepairInvoiceAction 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 5: Add the form button**

Read `repair_custom/views/repair_views.xml` to find the `<header>` of the main repair form view (search for `action_atelier_request_quote` to locate the right header block).

Add this button inside that `<header>` (next to existing action buttons, before `action_atelier_request_quote` or in a logical grouping):

```xml
<button name="action_invoice_repair_quote"
        type="object"
        string="Facturer le devis"
        class="btn-primary"
        invisible="not is_quote_invoiceable"/>
```

Also add the field declaration somewhere in the `<sheet>` (invisible) so the compute fires in the view:

```xml
<field name="is_quote_invoiceable" invisible="1"/>
```

Place it near other hidden fields (search for `invisible="1"` in the sheet).

- [ ] **Step 6: Verify the module upgrades cleanly**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --log-level=warn 2>&1 | tail -15`

Expected: clean upgrade, no XML parse errors.

- [ ] **Step 7: Commit**

```bash
git add repair_custom/models/repair_order.py repair_custom/views/repair_views.xml repair_custom/tests/test_quote_invoice_model.py
git commit -m "repair_custom: add 'Facturer le devis' button on repair form"
```

---

## Task 7: `repair.batch.action_invoice_approved_quotes` + form button

**Files:**
- Modify: `repair_custom/models/repair_batch.py`
- Modify: `repair_custom/views/repair_batch_views.xml`
- Test: `repair_custom/tests/test_quote_invoice_model.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_quote_invoice_model.py`:

```python
class TestBatchInvoiceAction(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair_a = self._make_repair()
        self.repair_b = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'B',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_a.batch_id.id,
        })
        self.repair_b._action_repair_confirm()
        self.so_a = self._make_sale_order_linked(self.repair_a)
        self.so_b = self._make_sale_order_linked(self.repair_b)
        self.batch = self.repair_a.batch_id

    def test_has_invoiceable_quotes_false_when_none_approved(self):
        self.assertFalse(self.batch.has_invoiceable_quotes)

    def test_has_invoiceable_quotes_true_when_any_approved(self):
        self.so_a.action_confirm()
        # Force recompute: depends on repair_ids.is_quote_invoiceable which
        # depends on quote_state + sale_order_id.invoice_status
        self.batch.invalidate_recordset(['has_invoiceable_quotes'])
        self.assertTrue(self.batch.has_invoiceable_quotes)

    def test_action_consolidates_only_approved(self):
        self.so_a.action_confirm()
        # so_b stays in draft → quote_state=pending, not eligible
        result = self.batch.action_invoice_approved_quotes()
        move = self.env['account.move'].browse(result['res_id'])
        # Only repair_a's lines should be on the move
        sos_on_move = move.invoice_line_ids.mapped('sale_line_ids.order_id')
        self.assertIn(self.so_a, sos_on_move)
        self.assertNotIn(self.so_b, sos_on_move)

    def test_action_raises_when_no_eligible(self):
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.batch.action_invoice_approved_quotes()
```

- [ ] **Step 2: Run — verify they fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestBatchInvoiceAction 2>&1 | tail -30`

Expected: all four FAIL.

- [ ] **Step 3: Implement field + action**

In `repair_custom/models/repair_batch.py`, add inside `RepairBatch`:

```python
    has_invoiceable_quotes = fields.Boolean(
        compute='_compute_has_invoiceable_quotes',
        string="Devis à facturer",
    )

    @api.depends('repair_ids.is_quote_invoiceable')
    def _compute_has_invoiceable_quotes(self):
        for batch in self:
            batch.has_invoiceable_quotes = any(
                r.is_quote_invoiceable for r in batch.repair_ids
            )

    def action_invoice_approved_quotes(self):
        """Batch-form button: consolidate all eligible approved quotes into
        one account.move."""
        self.ensure_one()
        eligible = self.repair_ids.filtered('is_quote_invoiceable')
        if not eligible:
            raise UserError(_(
                "Aucun devis accepté à facturer dans ce dossier."
            ))
        return self._invoice_approved_quotes(eligible)
```

- [ ] **Step 4: Run — verify they pass**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestBatchInvoiceAction 2>&1 | tail -15`

Expected: all four PASS.

- [ ] **Step 5: Add the batch form button**

Read `repair_custom/views/repair_batch_views.xml` to locate the main form view's `<header>` block.

Inside the `<header>`, add:

```xml
<button name="action_invoice_approved_quotes"
        type="object"
        string="Facturer les devis acceptés"
        class="btn-primary"
        invisible="not has_invoiceable_quotes"/>
```

In the `<sheet>` of the same view, add the field declaration (place it near other hidden fields):

```xml
<field name="has_invoiceable_quotes" invisible="1"/>
```

- [ ] **Step 6: Verify module upgrades**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --log-level=warn 2>&1 | tail -10`

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add repair_custom/models/repair_batch.py repair_custom/views/repair_batch_views.xml repair_custom/tests/test_quote_invoice_model.py
git commit -m "repair_custom: add 'Facturer les devis acceptés' button on batch form"
```

---

## Task 8: `sale.order.action_invoice_repair_quote` + native button replacement

**Files:**
- Modify: `repair_custom/models/repair_extensions.py` (SaleOrder class)
- Modify: `repair_custom/views/sale_order_views.xml` (from the placeholder created in Task 1)
- Test: `repair_custom/tests/test_quote_invoice_model.py`

- [ ] **Step 1: Write the failing test**

Append to `test_quote_invoice_model.py`:

```python
class TestSaleOrderButtonReplacement(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()
        self.so = self._make_sale_order_linked(self.repair)
        # Assign the repair quote template so computed_order_type = 'repair_quote'
        self.so.sale_order_template_id = self.env.ref(
            'repair_custom.sale_order_template_repair_quote'
        )
        self.so.action_confirm()

    def test_action_invoices_only_this_so(self):
        """Per-SO button (C.1) invoices only this SO even if batch siblings exist."""
        # Create a sibling repair with its own approved quote
        sibling = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Sibling',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair.batch_id.id,
        })
        sibling._action_repair_confirm()
        sibling_so = self._make_sale_order_linked(sibling)
        sibling_so.sale_order_template_id = self.env.ref(
            'repair_custom.sale_order_template_repair_quote'
        )
        sibling_so.action_confirm()

        result = self.so.action_invoice_repair_quote()
        move = self.env['account.move'].browse(result['res_id'])
        sos_on_move = move.invoice_line_ids.mapped('sale_line_ids.order_id')
        self.assertEqual(sos_on_move, self.so,
                         "Per-SO button must invoice only self, not siblings")

    def test_action_raises_without_repair_link(self):
        from odoo.exceptions import UserError
        standalone = self.SaleOrder.create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.service_product.id,
                'name': 'X',
                'product_uom_qty': 1,
                'price_unit': 1.0,
            })],
        })
        with self.assertRaises(UserError):
            standalone.action_invoice_repair_quote()

    def test_computed_order_type_is_repair_quote(self):
        """Sanity check for the view inheritance gate."""
        self.assertEqual(self.so.computed_order_type, 'repair_quote')
```

- [ ] **Step 2: Run — verify they fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestSaleOrderButtonReplacement 2>&1 | tail -20`

Expected: the first two FAIL with `AttributeError: action_invoice_repair_quote`. The third may pass already.

- [ ] **Step 3: Implement the SO action**

In `repair_custom/models/repair_extensions.py`, locate the `SaleOrder` class (around line 248). Add this method (place it near the existing `action_show_repair`, ~line 346):

```python
    def action_invoice_repair_quote(self):
        """Per-SO invoicing (C.1): invoices only this SO regardless of batch
        siblings. Routes through the batch consolidation helper with the SO's
        own repair_order_ids."""
        self.ensure_one()
        repairs = self.repair_order_ids
        if not repairs:
            raise UserError(_("Ce devis n'est lié à aucune réparation."))
        batch = repairs[:1].batch_id
        if not batch:
            raise UserError(_(
                "Ce devis n'est rattaché à aucun dossier de dépôt."
            ))
        return batch._invoice_approved_quotes(repairs)
```

Verify `UserError` and `_` are imported at the top of `repair_extensions.py`. If not, add:

```python
from odoo.exceptions import UserError
from odoo import _
```

- [ ] **Step 4: Run — verify actions pass**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestSaleOrderButtonReplacement 2>&1 | tail -15`

Expected: all three PASS.

- [ ] **Step 5: Fill in the sale.order view inheritance**

Open `repair_custom/views/sale_order_views.xml` (placeholder from Task 1) and replace with:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_order_form_repair_quote" model="ir.ui.view">
        <field name="name">sale.order.form.repair.quote</field>
        <field name="model">sale.order</field>
        <field name="inherit_id" ref="sale.view_order_form"/>
        <field name="arch" type="xml">
            <xpath expr="//button[@name='action_create_invoice']" position="attributes">
                <attribute name="invisible">computed_order_type == 'repair_quote'</attribute>
            </xpath>
            <xpath expr="//button[@name='action_create_invoice']" position="after">
                <button name="action_invoice_repair_quote"
                        type="object"
                        string="Facturer le devis"
                        class="btn-primary"
                        invisible="computed_order_type != 'repair_quote' or invoice_status not in ['to invoice', 'upselling']"/>
            </xpath>
        </field>
    </record>
</odoo>
```

- [ ] **Step 6: Upgrade module + verify**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --log-level=warn 2>&1 | tail -15`

Expected: clean. If `action_create_invoice` isn't found by xpath (button name may differ), adjust the xpath to match the actual button name from sale.view_order_form — you can verify by searching: `grep -n 'action_create_invoice\|Créer.*facture\|create_invoices' /Users/martin/Documents/odoo_dev/odoo/addons/sale/views/sale_views.xml`

- [ ] **Step 7: Commit**

```bash
git add repair_custom/models/repair_extensions.py repair_custom/views/sale_order_views.xml repair_custom/tests/test_quote_invoice_model.py
git commit -m "repair_custom: replace native invoice button on repair sale.orders"
```

---

## Task 9: Extend batch `action_mark_delivered` for refused quotes

**Files:**
- Modify: `repair_custom/models/repair_batch.py`
- Test: `repair_custom/tests/test_quote_invoice_model.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_quote_invoice_model.py`:

```python
class TestPartialAcceptancePickup(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair_ok = self._make_repair()
        self.repair_refused = self.Repair.create({
            'partner_id': self.partner.id,
            'internal_notes': 'Refused',
            'quote_required': True,
            'technician_employee_id': self.tech_with_user.id,
            'batch_id': self.repair_ok.batch_id.id,
        })
        self.repair_refused._action_repair_confirm()

        # Approve repair_ok's quote, refuse repair_refused's quote
        so_ok = self._make_sale_order_linked(self.repair_ok)
        so_ok.action_confirm()
        self.repair_ok.state = 'done'

        so_refused = self._make_sale_order_linked(self.repair_refused)
        so_refused.action_cancel()
        self.assertEqual(self.repair_refused.quote_state, 'refused')

        self.batch = self.repair_ok.batch_id

    def test_livrer_includes_refused_quote_repairs(self):
        self.batch.action_mark_delivered()
        self.assertEqual(self.repair_ok.delivery_state, 'delivered')
        self.assertEqual(self.repair_refused.delivery_state, 'delivered',
                         "Refused-quote repair picked up un-repaired")

    def test_refused_delivery_cancels_repair_state(self):
        self.batch.action_mark_delivered()
        self.assertEqual(self.repair_refused.state, 'cancel',
                         "Silent side effect: state -> cancel for refused pickup")

    def test_refused_delivery_leaves_approved_state_alone(self):
        self.batch.action_mark_delivered()
        self.assertEqual(self.repair_ok.state, 'done',
                         "Approved+done repair's state unchanged")

    def test_batch_delivery_state_reaches_delivered(self):
        self.batch.action_mark_delivered()
        self.batch.invalidate_recordset(['delivery_state'])
        self.assertEqual(self.batch.delivery_state, 'delivered')
```

- [ ] **Step 2: Run — verify they fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestPartialAcceptancePickup 2>&1 | tail -30`

Expected: `test_livrer_includes_refused_quote_repairs` FAILS — the current predicate excludes refused-quote repairs.

- [ ] **Step 3: Extend `action_mark_delivered`**

Read `repair_custom/models/repair_batch.py` around line 145 to see the current method.

Replace the current implementation with:

```python
    def action_mark_delivered(self):
        """Per-batch UI, per-repair data.

        Transitions all eligible repairs to delivered:
        - repairs in state {done, irreparable} with delivery_state='none'
        - repairs with quote_state='refused' and delivery_state='none'
          (client takes un-repaired device back; state silently set to cancel)

        Runs side effects via `action_repair_delivered`, marks the linked
        appointment done, and posts a chatter note.
        """
        self.ensure_one()
        eligible = self.repair_ids.filtered(
            lambda r: r.delivery_state == 'none'
            and (r.state in ('done', 'irreparable')
                 or r.quote_state == 'refused')
        )
        if not eligible:
            raise UserError(_(
                "Aucune réparation à livrer dans ce dossier."
            ))

        # Partial-acceptance branch: refused-quote repairs go out un-repaired.
        # Silent state='cancel' side effect + delivery_state='delivered';
        # no SAR, no invoice (no approved SO to invoice from).
        refused_pickup = eligible.filtered(
            lambda r: r.quote_state == 'refused'
            and r.state not in ('cancel', 'irreparable')
        )
        for rec in refused_pickup:
            rec.state = 'cancel'
        refused_pickup.write({'delivery_state': 'delivered'})

        normal_pickup = eligible - refused_pickup
        if normal_pickup:
            normal_pickup.action_repair_delivered()

        if (self.current_appointment_id
                and self.current_appointment_id.state == 'scheduled'):
            self.current_appointment_id.action_mark_done()

        self.message_post(body=_(
            "Dossier livré : %d appareil(s) remis au client."
        ) % len(eligible))
        return True
```

- [ ] **Step 4: Run — verify all pass**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestPartialAcceptancePickup 2>&1 | tail -15`

Expected: all four PASS.

- [ ] **Step 5: Run the full Theme B batch tests to verify no regression**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestBatchDeliveryState 2>&1 | tail -15`

Expected: all Theme B delivery-state tests still PASS. If one breaks because it previously relied on the stricter predicate, read the failure and adjust the Theme B test (document the adjustment in the commit message).

- [ ] **Step 6: Commit**

```bash
git add repair_custom/models/repair_batch.py repair_custom/tests/test_quote_invoice_model.py
git commit -m "repair_custom: extend batch Livrer to handle refused-quote pickup"
```

---

## Task 10: Trim `repair.pricing.wizard` (Python) to quote-only

**Files:**
- Modify: `repair_custom/wizard/repair_pricing_wizard.py`
- Test: `repair_custom/tests/test_quote_invoice_model.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_quote_invoice_model.py`:

```python
class TestPricingWizardQuoteOnly(RepairQuoteCase):

    def setUp(self):
        super().setUp()
        self.repair = self._make_repair()

    def test_wizard_has_no_generation_type_field(self):
        Wizard = self.env['repair.pricing.wizard']
        self.assertNotIn('generation_type', Wizard._fields,
                         "generation_type removed in Theme A")

    def test_wizard_has_no_batch_fields(self):
        Wizard = self.env['repair.pricing.wizard']
        for f in ('batch_id', 'remaining_repair_ids',
                  'accumulated_lines_json', 'step_info'):
            self.assertNotIn(f, Wizard._fields,
                             f"{f} removed in Theme A")

    def test_wizard_creates_quote_only(self):
        wizard = self.env['repair.pricing.wizard'].with_context(
            default_repair_id=self.repair.id
        ).create({
            'repair_id': self.repair.id,
            'target_total_amount': 100.0,
            'manual_product_id': self.service_product.id,
            'manual_label': 'Forfait test',
        })
        result = wizard.action_confirm()
        self.assertEqual(result['res_model'], 'sale.order',
                         "Wizard produces a sale.order, not an account.move")
        so = self.env['sale.order'].browse(result['res_id'])
        self.assertEqual(so, self.repair.sale_order_id)
        self.assertEqual(so.sale_order_template_id,
                         self.env.ref('repair_custom.sale_order_template_repair_quote'))

    def test_wizard_rejects_duplicate_quote(self):
        from odoo.exceptions import UserError
        self._make_sale_order_linked(self.repair)
        wizard = self.env['repair.pricing.wizard'].create({
            'repair_id': self.repair.id,
            'target_total_amount': 50.0,
            'manual_product_id': self.service_product.id,
            'manual_label': 'Double',
        })
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_wizard_ignores_batch_context(self):
        """Launching with active_model='repair.batch' no longer pre-fills a
        batch walkthrough — Theme A removes that entry path."""
        wizard_env = self.env['repair.pricing.wizard'].with_context(
            active_model='repair.batch',
            active_id=self.repair.batch_id.id,
            default_repair_id=self.repair.id,
        )
        # default_get should not populate anything batch-shaped (fields don't exist)
        # and should still resolve the repair_id from default_repair_id
        defaults = wizard_env.default_get(['repair_id', 'device_name'])
        self.assertEqual(defaults.get('repair_id'), self.repair.id)
```

- [ ] **Step 2: Run — verify they fail**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestPricingWizardQuoteOnly 2>&1 | tail -25`

Expected: field-existence tests FAIL (fields still exist); the others may pass in invoice mode or fail because the wizard defaults to invoice generation. Key proof: first two fail.

- [ ] **Step 3: Rewrite the wizard**

Replace the entire contents of `repair_custom/wizard/repair_pricing_wizard.py` with:

```python
import logging

from odoo import models, fields, api, _, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class RepairPricingWizard(models.TransientModel):
    _name = 'repair.pricing.wizard'
    _description = "Calculatrice de Prix et Ventilation (Devis)"

    repair_id = fields.Many2one('repair.order', required=True)
    internal_notes = fields.Text(string="Notes technicien", readonly=True)

    # --- CONFIGURATION ---
    use_template = fields.Boolean("Utiliser un modèle", default=False)
    invoice_template_id = fields.Many2one(
        'repair.invoice.template', string="Modèle de Facturation"
    )

    target_total_amount = fields.Monetary(
        "Total HT Souhaité", required=True, currency_field='currency_id'
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id
    )

    extra_parts_ids = fields.One2many(
        'repair.pricing.part', 'wizard_id', string="Pièces Spécifiques"
    )
    parts_mode = fields.Selection([
        ('included', 'Déduire du Total'),
        ('added', 'Ajouter au Total'),
    ], string="Gestion des pièces", default='included', required=True)

    manual_label = fields.Char(
        "Libellé de la ligne", default="Forfait Atelier / Main d'œuvre"
    )
    manual_product_id = fields.Many2one(
        'product.product',
        string="Article Service",
        domain=[('type', '=', 'service')],
        help="Article utilisé pour la ligne de facturation libre",
    )

    # --- DÉTAILS / NOTES ---
    device_name = fields.Char(string="Appareil", readonly=True)
    technician_employee_id = fields.Many2one(
        'hr.employee', string="Technicien", readonly=True
    )
    work_time = fields.Float(related="repair_id.work_time", readonly=True)
    add_work_details = fields.Boolean(
        "Ajouter le détail des travaux", default=True
    )
    work_details = fields.Text("Détail à afficher sur la facture")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        service = self.env['product.product'].search(
            [('type', '=', 'service'), ('default_code', '=', 'SERV')], limit=1
        )
        if not service:
            service = self.env['product.product'].search(
                [('type', '=', 'service')], limit=1
            )
        if service:
            res['manual_product_id'] = service.id

        active_repair_id = (self.env.context.get('default_repair_id')
                            or self.env.context.get('active_id'))
        # Theme A: batch walkthrough removed. active_model='repair.batch' is
        # ignored; caller must pass default_repair_id explicitly.
        if active_repair_id:
            repair = self.env['repair.order'].browse(active_repair_id)
            if repair.exists():
                clean_notes = tools.html2plaintext(
                    repair.internal_notes or ""
                ).strip()
                res['repair_id'] = repair.id
                res['work_details'] = clean_notes
                res['internal_notes'] = clean_notes
                res['device_name'] = repair.device_id_name
                res['technician_employee_id'] = (
                    repair.technician_employee_id.id or False
                )
                res['work_time'] = repair.work_time

        return res

    def action_confirm(self):
        self.ensure_one()
        lines = self._get_invoice_lines_formatted()
        try:
            with self.env.cr.savepoint():
                return self._create_quote(lines)
        except Exception as e:
            _logger.error("Failed to create quote: %s", e)
            raise UserError(_("Erreur lors de la création du devis : %s") % e)

    def _get_invoice_lines_formatted(self):
        """Generate the list of line dicts for the sale.order: one header
        section + N product lines + optional notes section."""
        lines_data = self._prepare_lines_data()
        invoice_lines_vals = []

        invoice_lines_vals.append({
            'display_type': 'line_section',
            'name': self._get_header_label(),
            'product_id': False,
        })

        for line in lines_data:
            invoice_lines_vals.append({
                'display_type': 'product',
                'product_id': line['product_id'],
                'name': line['name'],
                'quantity': line['quantity'],
                'price_unit': line['price_unit'],
                'tax_ids': line['tax_ids'],
            })

        if self.add_work_details and self.work_details:
            invoice_lines_vals.append({
                'display_type': 'line_section',
                'name': "Détails",
                'product_id': False,
            })
            invoice_lines_vals.append({
                'display_type': 'line_note',
                'name': self.work_details,
                'product_id': False,
            })

        return invoice_lines_vals

    def _prepare_lines_data(self):
        """HT amount distribution between parts and labour."""
        total_parts_ht = sum(p.price_subtotal for p in self.extra_parts_ids)

        if self.parts_mode == 'included':
            work_amount_ht = self.target_total_amount - total_parts_ht
            if work_amount_ht < 0:
                raise UserError(_(
                    "Le montant des pièces (%s HT) dépasse le total souhaité "
                    "(%s HT) !"
                ) % (total_parts_ht, self.target_total_amount))
        else:
            work_amount_ht = self.target_total_amount

        lines_list = []

        for part in self.extra_parts_ids:
            lines_list.append({
                'product_id': part.product_id.id,
                'name': part.name or part.product_id.name,
                'quantity': part.quantity,
                'price_unit': part.price_unit,
                'tax_ids': part.product_id.taxes_id.ids,
            })

        if self.use_template:
            if not self.invoice_template_id:
                raise UserError(_(
                    "Veuillez sélectionner un modèle de facturation."
                ))
            total_weight = sum(
                l.weight_percentage for l in self.invoice_template_id.line_ids
            )
            if total_weight == 0:
                raise UserError(_(
                    "Le modèle doit avoir des pourcentages > 0."
                ))
            for t_line in self.invoice_template_id.line_ids:
                share = t_line.weight_percentage / total_weight
                lines_list.append({
                    'product_id': t_line.product_id.id,
                    'name': t_line.name,
                    'quantity': 1,
                    'price_unit': work_amount_ht * share,
                    'tax_ids': t_line.product_id.taxes_id.ids,
                })
        else:
            if not self.manual_product_id:
                raise UserError(_(
                    "Veuillez sélectionner un Article Service."
                ))
            lines_list.append({
                'product_id': self.manual_product_id.id,
                'name': self.manual_label,
                'quantity': 1,
                'price_unit': work_amount_ht,
                'tax_ids': self.manual_product_id.taxes_id.ids,
            })

        return lines_list

    def _get_header_label(self):
        device_name = self.device_name or "Appareil Inconnu"
        sn = self.repair_id.serial_number
        label = f"Réparation : {device_name}"
        if sn:
            label += f" (S/N: {sn})"
        return label

    def _create_quote(self, lines_list_dicts):
        """Create exactly one sale.order linked to self.repair_id."""
        if self.repair_id.sale_order_id:
            raise UserError(_("Un devis est déjà lié à cette réparation."))

        formatted_lines = []
        for l in lines_list_dicts:
            raw_type = l.get('display_type', False)
            dtype = False if raw_type == 'product' else raw_type
            val = {
                'display_type': dtype,
                'name': l['name'],
                'product_id': l['product_id'],
            }
            if dtype == 'product' or not dtype:
                val.update({
                    'product_uom_qty': l['quantity'],
                    'price_unit': l['price_unit'],
                    'tax_id': [(6, 0, l['tax_ids'])],
                })
            formatted_lines.append((0, 0, val))

        template = self.env.ref(
            'repair_custom.sale_order_template_repair_quote'
        )
        sale_order = self.env['sale.order'].create({
            'partner_id': self.repair_id.partner_id.id,
            'order_line': formatted_lines,
            'sale_order_template_id': template.id,
            'repair_order_ids': [(4, self.repair_id.id)],
        })
        self.repair_id.sale_order_id = sale_order.id

        return {
            'name': _("Devis Généré"),
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': sale_order.id,
            'view_mode': 'form',
        }


class RepairPricingPart(models.TransientModel):
    _name = 'repair.pricing.part'
    _description = "Ligne de pièce manuelle"

    wizard_id = fields.Many2one('repair.pricing.wizard', string="Wizard Lien")
    product_id = fields.Many2one(
        'product.product', string="Pièce", required=True,
        domain=[('type', '!=', 'service')],
    )
    name = fields.Char("Description")
    quantity = fields.Float(default=1.0)
    price_unit = fields.Float("Prix Unit. HT", required=True)
    price_subtotal = fields.Float(compute='_compute_sub', string="Total HT")

    @api.depends('quantity', 'price_unit')
    def _compute_sub(self):
        for rec in self:
            rec.price_subtotal = rec.quantity * rec.price_unit

    @api.onchange('product_id')
    def _onchange_product(self):
        if self.product_id:
            self.price_unit = self.product_id.lst_price
            self.name = self.product_id.name
```

- [ ] **Step 4: Run — verify the wizard tests pass**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:TestPricingWizardQuoteOnly 2>&1 | tail -20`

Expected: all four PASS. The module may fail to upgrade because the form view still references the deleted fields — that's expected and fixed in Task 11.

If the upgrade itself errored out before tests ran, jump to Task 11 first, then come back to re-run this step.

- [ ] **Step 5: Commit**

```bash
git add repair_custom/wizard/repair_pricing_wizard.py repair_custom/tests/test_quote_invoice_model.py
git commit -m "repair_custom: trim pricing wizard to quote-only (drop invoice mode, batch walkthrough)"
```

---

## Task 11: Trim `repair.pricing.wizard` view

**Files:**
- Modify: `repair_custom/views/repair_pricing_wizard_views.xml`

- [ ] **Step 1: Replace the wizard view XML**

Read the current file (`repair_custom/views/repair_pricing_wizard_views.xml`), then replace with:

```xml
<odoo>
    <record id="view_repair_pricing_wizard_form" model="ir.ui.view">
        <field name="name">repair.pricing.wizard.form</field>
        <field name="model">repair.pricing.wizard</field>
        <field name="arch" type="xml">
            <form string="Création du Devis">
                <sheet>
                    <group>
                        <group string="Contexte de l'Intervention">
                            <field name="device_name" readonly="1" style="font-weight: bold;"/>
                            <field name="technician_employee_id" readonly="1"/>
                            <field name="work_time" widget="float_time" readonly="1"/>
                            <field name="internal_notes"
                                readonly="1"
                                style="max-height: 150px; overflow-y: auto; background: #f0f0f0; padding: 5px; font-family: monospace;"/>
                        </group>

                        <group string="Tarification">
                            <field name="use_template" widget="boolean_toggle"/>
                            <field name="invoice_template_id"
                                invisible="not use_template"
                                required="use_template"
                                domain="['|', ('device_category_ids', '=', False), ('device_category_ids', 'parent_of', context.get('default_device_categ_id'))]"
                                options="{'no_create': True}"
                                placeholder="Sélectionnez un modèle de ventilation"/>
                            <field name="manual_label"
                                invisible="use_template"
                                required="not use_template"
                                placeholder="Intitulé de la ligne unique"/>
                            <field name="manual_product_id"
                                invisible="use_template"
                                required="not use_template"
                                options="{'no_create': True}"
                                placeholder="Article service comptable"/>
                            <label for="target_total_amount" string="Montant TOTAL (HT)"/>
                            <field name="target_total_amount" nolabel="1" widget="monetary" style="font-size: 22px; font-weight: 700; color: #2e8b57;"/>
                            <field name="currency_id" invisible="1"/>
                        </group>
                    </group>

                    <notebook>
                        <page string="Rapport d'intervention" name="report_page">
                            <group>
                                <label for="add_work_details" string="Inclure le rapport" style="font-weight: bold;"/>
                                <field name="add_work_details" nolabel="1" widget="boolean_toggle"/>
                            </group>
                            <field name="work_details"
                                   invisible="not add_work_details"
                                   placeholder="Le texte qui s'affichera en bas de la facture..."
                                   style="background: #f0f0f0; font-family: monospace; padding: 10px; border: 1px solid #ccc; min-height: 150px;"/>
                        </page>
                        <page string="Pièces &amp; Fournitures" name="parts_page">
                            <group>
                                <field name="parts_mode" widget="radio" options="{'horizontal': true}"/>
                            </group>
                            <field name="extra_parts_ids">
                                <tree editable="bottom">
                                    <field name="product_id"/>
                                    <field name="name"/>
                                    <field name="quantity"/>
                                    <field name="price_unit" string="P.U. (HT)"/>
                                    <field name="price_subtotal" readonly="1" sum="Total Pièces HT"/>
                                </tree>
                            </field>
                        </page>
                    </notebook>
                </sheet>
                <footer class="justify-content-end">
                    <button string="Valider" type="object" name="action_confirm" class="btn-success"/>
                    <button string="Annuler" special="cancel" class="btn-secondary"/>
                </footer>
            </form>
        </field>
    </record>

    <record id="action_repair_pricing_wizard" model="ir.actions.act_window">
        <field name="name">Devis</field>
        <field name="res_model">repair.pricing.wizard</field>
        <field name="view_mode">form</field>
        <field name="target">new</field>
    </record>
</odoo>
```

Differences from the original: `<field name="batch_id" invisible="1"/>`, `<field name="remaining_repair_ids" invisible="1"/>`, `<field name="accumulated_lines_json" invisible="1"/>`, the batch alert div, the `generation_type` radio, and the `action_next_step` button are all gone. Action `name` is "Devis" instead of "Facturer".

- [ ] **Step 2: Upgrade + verify**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --log-level=warn 2>&1 | tail -15`

Expected: clean upgrade. If an error points at another view (repair form or batch form still referencing wizard entry in invoice mode), note the error and fix in Task 12.

- [ ] **Step 3: Commit**

```bash
git add repair_custom/views/repair_pricing_wizard_views.xml
git commit -m "repair_custom: strip batch/invoice-mode UI from pricing wizard form"
```

---

## Task 12: Retire obsolete wizard entry points in repair views

**Files:**
- Modify: `repair_custom/views/repair_views.xml`
- Modify: `repair_custom/models/repair_order.py`
- Modify: `repair_custom/models/repair_batch.py` (if `action_pickup_start` references `default_mode='invoice'`)

- [ ] **Step 1: Audit remaining references**

Run:

```bash
grep -rn "action_open_pricing_wizard\|default_generation_type\|default_mode.*invoice\|active_model.*repair\.batch" repair_custom/ 2>&1
```

Expected hits:
- `repair_custom/views/repair_views.xml:78` — `%(action_repair_pricing_wizard)d` button "Devis/Facture" (may or may not open in invoice mode)
- `repair_custom/views/repair_views.xml:198` — `action_open_pricing_wizard` button "Devis/Facture"
- `repair_custom/views/repair_views.xml:241` — `action_create_quotation_wizard` button "Créer le devis"
- `repair_custom/models/repair_order.py:1002` — `action_open_pricing_wizard` method definition
- `repair_custom/models/repair_order.py:1131` — `'default_generation_type': 'quote'` context key
- `repair_custom/models/repair_batch.py:141` — `'default_mode': 'invoice'` inside `action_pickup_start`

- [ ] **Step 2: Delete `action_open_pricing_wizard` from `repair.order`**

Read `repair_custom/models/repair_order.py` around line 1002 to confirm the method. Delete the entire method (approximately 14 lines, from `def action_open_pricing_wizard(self):` through its closing `}`).

- [ ] **Step 3: Strip `default_generation_type` context key**

In `action_create_quotation_wizard` (around line 1118), remove the `'default_generation_type': 'quote',` line from the context dict. The method becomes:

```python
    def action_create_quotation_wizard(self):
        self.ensure_one()
        device_categ_id = (
            self.product_tmpl_id.categ_id.id if self.product_tmpl_id else False
        )
        return {
            'name': _("Création du Devis"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.pricing.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_repair_id': self.id,
                'default_device_categ_id': device_categ_id,
            },
        }
```

- [ ] **Step 4: Rename obsolete "Devis/Facture" buttons in `repair_views.xml`**

Read `repair_custom/views/repair_views.xml` at lines 78 and 198. Both are buttons that should be retired or redirected.

For the button at line ~78 (uses `%(action_repair_pricing_wizard)d`):
Replace the button with the `action_create_quotation_wizard` call and relabel:

```xml
<button name="action_create_quotation_wizard" icon="fa-file"
        string="Devis" type="object"
        invisible="state == 'draft' or sale_order_id"/>
```

For the button at line ~198 (`action_open_pricing_wizard`):
Replace with the same pattern:

```xml
<button name="action_create_quotation_wizard" icon="fa-file"
        string="Devis" type="object"
        invisible="state == 'draft' or sale_order_id"/>
```

The button at ~241 (`action_create_quotation_wizard`, label "Créer le devis") can stay — it's already the quote-creation entry. Just verify its `invisible=` doesn't gate on `quote_state` alone. If it does, relax to match the Theme A rule:

```xml
invisible="state == 'draft' or sale_order_id"
```

(This implements decision #8 clarification: the wizard is available whenever a repair exists and no SO is linked, regardless of `quote_state`.)

- [ ] **Step 5: Strip `default_mode='invoice'` from `action_pickup_start`**

Read `repair_custom/models/repair_batch.py` around line 107-143. The `action_pickup_start` method's fallback branch (no SO) opens the wizard. With the wizard now quote-only, this branch should no longer force invoice mode. Change the context to just:

```python
        return {
            'name': _("Création du Devis"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.pricing.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_repair_id': eligible[:1].id,
            },
        }
```

- [ ] **Step 6: Upgrade + verify views load**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --log-level=warn 2>&1 | tail -20`

Expected: clean. Any remaining error about a missing field on the wizard form → audit that specific view.

- [ ] **Step 7: Run the full repair_custom test suite**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom 2>&1 | tail -40`

Expected: every test under `test_quote_invoice_model.py` passes. Some tests in `test_quote_lifecycle.py` may fail — those are addressed in Task 13. Note any non-lifecycle failures and fix them inline before committing.

- [ ] **Step 8: Commit**

```bash
git add repair_custom/views/repair_views.xml repair_custom/models/repair_order.py repair_custom/models/repair_batch.py
git commit -m "repair_custom: retire wizard invoice-mode entry points"
```

---

## Task 13: Sub-project 2 test sweep

**Files:**
- Modify: `repair_custom/tests/test_quote_lifecycle.py`

- [ ] **Step 1: Run the sub-project 2 suite to see what breaks**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:test_quote_lifecycle 2>&1 | tail -40`

If all tests pass, skip to Step 4 (commit a note that no changes were needed).

- [ ] **Step 2: Audit for grouped-SO assertions**

Run:

```bash
grep -n "batch_id\|repair_order_ids.*,.*repair\|repair_ids.write.*sale_order\|batch.*sale_order_id\|_create_global_invoice\|generation_type" repair_custom/tests/test_quote_lifecycle.py
```

Any test that asserts a grouped-SO shape (one SO linked to N repairs of a batch) contradicts Theme A's model. Candidate fix: either delete the test (if its behavior is obsolete) or rewrite it to create N SOs and assert per-repair sync.

Specifically check `test_write_hook_batch_sale_order_syncs_all_repairs` (spec's migration note). If this test exists, its grouped-SO setup is now an unsupported shape — rewrite:

```python
    def test_each_repair_sync_is_independent(self):
        """Theme A: per-repair SOs, each syncs its own repair independently."""
        # Build two repairs in the same batch, each with its own SO
        self.repair._apply_quote_state_transition('pending')
        so_a = self._make_sale_order_linked(self.repair)
        sibling = self._make_repair()
        sibling.batch_id = self.repair.batch_id
        so_b = self._make_sale_order_linked(sibling)

        so_a.action_confirm()  # syncs self.repair only
        self.assertEqual(self.repair.quote_state, 'approved')
        self.assertNotEqual(sibling.quote_state, 'approved',
                            "Sibling with its own SO stays pending")
```

- [ ] **Step 3: Rerun the suite after fixes**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom:test_quote_lifecycle 2>&1 | tail -20`

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add repair_custom/tests/test_quote_lifecycle.py
git commit -m "repair_custom: reconcile sub-project 2 tests with per-repair SO model"
```

(If no changes were needed, skip the commit and proceed to Task 14.)

---

## Task 14: Full test suite green + manifest sanity

**Files:** none (verification only)

- [ ] **Step 1: Run every repair_custom test**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf --stop-after-init -u repair_custom --test-enable --test-tags /repair_custom 2>&1 | tee /tmp/theme_a_test_run.log | tail -60`

Expected: every test passes. If any fail, read `/tmp/theme_a_test_run.log`, diagnose, and fix inline (don't move on with a red suite).

- [ ] **Step 2: Verify manifest version**

Read `repair_custom/__manifest__.py` and confirm `'version': '17.0.1.7.0'`.

- [ ] **Step 3: Verify the new view file is registered**

Run: `grep -n 'sale_order_views' repair_custom/__manifest__.py`

Expected: one hit inside the `'data'` list.

- [ ] **Step 4: Final commit (if fixes were needed in step 1)**

```bash
git add -u
git commit -m "repair_custom: final Theme A polish — full suite green" || echo "nothing to commit"
```

---

## Task 15: Manual QA run-through

**Files:** none (manual testing only)

- [ ] **Step 1: Start Odoo in dev mode**

Run: `cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --dev=reload,xml`

- [ ] **Step 2: Execute the spec's manual QA checklist**

Follow the 10 items from the spec's "Manual QA checklist" section (in `docs/superpowers/specs/2026-04-22-repair-quote-invoice-model-design.md`). Key scenarios:

1. Multi-device batch, one accepted + one refused: verify consolidated invoice excludes refused; verify `Livrer` delivers both.
2. Walk-in (no tech diagnosis → `quote_state='none'`): verify "Devis" button visible; run wizard → approve → invoice.
3. Native `sale.order` form on a repair quote: verify "Créer la facture" is hidden, "Facturer le devis" visible.
4. Legacy multi-repair SO (if any in the DB): verify batch button still invoices cleanly with SO-name fallback header.

- [ ] **Step 3: Record any issues**

For each failing QA item, either fix inline and re-run, or document as a follow-up issue.

- [ ] **Step 4: Final sanity commit**

If any fixes were made:

```bash
git add -u
git commit -m "repair_custom: manual QA fixes for Theme A"
```

---

## Self-Review

Checking the plan against the spec:

**Spec section coverage:**

| Spec section | Covered by |
|---|---|
| §1 Pricing wizard quote-only | Task 10 (Python) + Task 11 (XML) |
| §2 Facturer le devis flow (repair side) | Task 5 (field) + Task 6 (action/button) |
| §2 Facturer le devis flow (batch side) | Task 7 |
| §2 Core helper `_invoice_approved_quotes` | Task 4 |
| §2 Section header injection | Task 3 |
| §3 Native sale.order button replacement | Task 8 |
| §4 account.move auto-stamp | Task 2 |
| §5 Partial acceptance pickup | Task 9 |
| Obsolete entry point retirement | Task 12 |
| Sub-project 2 test reconciliation | Task 13 |
| Manifest bump + new view registration | Task 1 |
| Final verification | Tasks 14-15 |

All spec sections accounted for.

**Type / signature consistency check:**
- `is_quote_invoiceable` (Task 5) → referenced in Task 6 (repair button), Task 7 (batch compute). Spellings match.
- `has_invoiceable_quotes` (Task 7) → used in batch view button. Matches.
- `_invoice_approved_quotes(repairs)` signature: Task 4 defines one positional arg. Task 6 calls `self.batch_id._invoice_approved_quotes(self)` — singleton recordset. Task 7 calls with `eligible` filtered recordset. Task 8 calls with `self.repair_order_ids`. All compatible.
- `_inject_repair_section_headers(move)` signature: Task 3 defines, Task 4 calls. Matches.
- `_auto_stamp_repair_metadata` (Task 2) → self-contained, called from `create`.
- `action_invoice_repair_quote` appears on both `repair.order` (Task 6) and `sale.order` (Task 8). Different models, same name — fine in Odoo (no collision).

**Placeholder scan:** no "TBD", "TODO", or skeleton-only tests found. Every step has runnable commands or full code.

**Ambiguity check:** one soft spot — in Task 8 Step 6 the xpath targets `//button[@name='action_create_invoice']`. If Odoo's upstream `sale.view_order_form` uses a different button name, the instruction includes the grep command to verify.

Plan ready.
