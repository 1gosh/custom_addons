# Device / Lot Edit Propagation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate stale device/serial labels on repair orders by (a) replacing the `serial_number` Char with the existing `lot_id` Many2one as the intake field, (b) making `device_id_name` non-stored, (c) ensuring edits to `product.template` and `stock.lot.name` propagate live.

**Architecture:** All display chains already route through `product.template.display_name` and `stock.lot.display_name` (both non-stored, live). The source of drift is two cached fields on `repair.order` (`serial_number`, `device_id_name`). We remove/unstore them and promote `lot_id` to the editable intake field with product-scoped autocomplete and create-on-the-fly. Existing quant/picking logic in `action_validate` stays intact.

**Tech Stack:** Odoo 17, Python 3.10+, PostgreSQL, XML views/reports.

---

## Pre-flight

- [ ] **Step 0: Confirm branch**

Run: `git status && git branch --show-current`
Expected: branch `feature/device-lot-edit-propagation`, clean tree.

---

### Task 1: Bump module version and add migration scaffold

**Files:**
- Modify: `repair_custom/__manifest__.py`
- Create: `repair_custom/migrations/17.0.1.8.0/pre-migrate.py`
- Create: `repair_custom/migrations/17.0.1.8.0/post-migrate.py`

- [ ] **Step 1: Bump version**

Edit `repair_custom/__manifest__.py` — change `'version': '17.0.1.7.0'` → `'version': '17.0.1.8.0'`.

- [ ] **Step 2: Create pre-migrate audit script**

Create `repair_custom/migrations/17.0.1.8.0/pre-migrate.py`:

```python
# -*- coding: utf-8 -*-
"""Audit divergences between cached serial_number and lot.name before drop."""
import logging
_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT ro.id, ro.name, ro.serial_number, sl.name AS lot_name
        FROM repair_order ro
        LEFT JOIN stock_lot sl ON sl.id = ro.lot_id
        WHERE ro.serial_number IS NOT NULL
          AND ro.serial_number != ''
          AND ro.lot_id IS NOT NULL
          AND ro.serial_number != COALESCE(sl.name, '')
    """)
    rows = cr.fetchall()
    if rows:
        _logger.warning(
            "pre-migrate 17.0.1.8.0: %d repair orders have serial_number "
            "diverging from lot.name. Truth will be lot.name after migration.",
            len(rows),
        )
        for ro_id, ro_name, sn, lot_name in rows[:20]:
            _logger.warning("  repair id=%s ref=%s sn=%r lot_name=%r",
                            ro_id, ro_name, sn, lot_name)
```

- [ ] **Step 3: Create post-migrate backfill script**

Create `repair_custom/migrations/17.0.1.8.0/post-migrate.py`:

```python
# -*- coding: utf-8 -*-
"""Backfill lot_id for any draft repair that still has only serial_number."""
import logging
_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Check column still exists (pre module field removal in the same release)
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='repair_order' AND column_name='serial_number'
    """)
    if not cr.fetchone():
        _logger.info("post-migrate 17.0.1.8.0: serial_number column already dropped, skipping")
        return

    # 1. Match existing lots by (product, serial) where lot_id is empty
    cr.execute("""
        UPDATE repair_order ro
        SET lot_id = sl.id
        FROM stock_lot sl, product_product pp
        WHERE ro.lot_id IS NULL
          AND ro.serial_number IS NOT NULL
          AND ro.serial_number != ''
          AND ro.product_tmpl_id IS NOT NULL
          AND pp.product_tmpl_id = ro.product_tmpl_id
          AND sl.product_id = pp.id
          AND sl.name = ro.serial_number
    """)
    _logger.info("post-migrate 17.0.1.8.0: linked %d repairs to existing lots", cr.rowcount)

    # 2. Create lots for draft repairs with a serial_number but no matching lot
    cr.execute("""
        SELECT ro.id, ro.serial_number, ro.product_tmpl_id, ro.company_id,
               ro.partner_id, ro.variant_id, pp.id AS product_id
        FROM repair_order ro
        JOIN product_product pp ON pp.product_tmpl_id = ro.product_tmpl_id
        WHERE ro.lot_id IS NULL
          AND ro.serial_number IS NOT NULL
          AND ro.serial_number != ''
          AND ro.state = 'draft'
    """)
    rows = cr.fetchall()
    created = 0
    for ro_id, sn, _tmpl, company_id, partner_id, variant_id, product_id in rows:
        cr.execute("""
            INSERT INTO stock_lot (name, product_id, company_id, hifi_partner_id,
                                   hifi_variant_id, create_date, write_date,
                                   create_uid, write_uid)
            VALUES (%s, %s, %s, %s, %s, now(), now(), 1, 1)
            RETURNING id
        """, (sn, product_id, company_id, partner_id, variant_id))
        new_lot_id = cr.fetchone()[0]
        cr.execute("UPDATE repair_order SET lot_id = %s WHERE id = %s", (new_lot_id, ro_id))
        created += 1
    _logger.info("post-migrate 17.0.1.8.0: created %d new lots for draft repairs", created)
```

- [ ] **Step 4: Commit**

```bash
git add repair_custom/__manifest__.py repair_custom/migrations/17.0.1.8.0/
git commit -m "repair_custom: bump to 17.0.1.8.0 with serial_number→lot_id migration scaffold"
```

---

### Task 2: Make `device_id_name` non-stored

**Files:**
- Modify: `repair_custom/models/repair_order.py:394`

- [ ] **Step 1: Drop `store=True`**

Edit `repair_custom/models/repair_order.py` line 394.

Before:
```python
device_id_name = fields.Char("Appareil", compute="_compute_device_id_name", store=True, readonly=True)
```

After:
```python
device_id_name = fields.Char("Appareil", compute="_compute_device_id_name", readonly=True)
```

The `@api.depends(...)` on `_compute_device_id_name` (line 397) stays as-is — Odoo uses it for cache invalidation even on non-stored fields.

- [ ] **Step 2: Restart Odoo with module update and verify no error**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

Expected: module upgrades cleanly, no tracebacks.

- [ ] **Step 3: Commit**

```bash
git add repair_custom/models/repair_order.py
git commit -m "repair_custom: unstore device_id_name to avoid stale display cache"
```

---

### Task 3: Promote `lot_id` to editable intake field

**Files:**
- Modify: `repair_custom/models/repair_order.py:392-393`
- Modify: `repair_custom/models/repair_order.py:451-457` (onchange)
- Modify: `repair_custom/models/repair_order.py:414-422` (onchange product_tmpl)

- [ ] **Step 1: Remove `readonly=True` from `lot_id`**

Edit line 392-393.

Before:
```python
    lot_id = fields.Many2one('stock.lot', string="Appareil physique", readonly=True, index=True,
                              domain=[('is_hifi_unit', '=', True)])
```

After:
```python
    lot_id = fields.Many2one(
        'stock.lot', string="Appareil physique",
        index=True,
        domain=[('is_hifi_unit', '=', True)],
        help="Unité physique. Tape un numéro de série existant pour le retrouver, "
             "ou un nouveau numéro pour le créer à la volée.",
    )
```

- [ ] **Step 2: Drop `serial_number` assignments from onchanges**

Edit `_onchange_product_tmpl_id_clear_variant` (lines 414-422).

Before:
```python
    @api.onchange('product_tmpl_id')
    def _onchange_product_tmpl_id_clear_variant(self):
        if self.lot_id and self.product_tmpl_id == self.lot_id.product_id.product_tmpl_id:
            return
        if self.product_tmpl_id:
            self.variant_id = False
            if self.lot_id:
                self.lot_id = False
                self.serial_number = False
```

After:
```python
    @api.onchange('product_tmpl_id')
    def _onchange_product_tmpl_id_clear_variant(self):
        if self.lot_id and self.product_tmpl_id == self.lot_id.product_id.product_tmpl_id:
            return
        if self.product_tmpl_id:
            self.variant_id = False
            if self.lot_id:
                self.lot_id = False
```

Edit `_onchange_lot_id` (lines 451-457).

Before:
```python
    @api.onchange('lot_id')
    def _onchange_lot_id(self):
        for rec in self:
            if rec.lot_id:
                rec.serial_number = rec.lot_id.name
                rec.product_tmpl_id = rec.lot_id.product_id.product_tmpl_id
                rec.variant_id = rec.lot_id.hifi_variant_id
```

After:
```python
    @api.onchange('lot_id')
    def _onchange_lot_id(self):
        for rec in self:
            if rec.lot_id:
                rec.product_tmpl_id = rec.lot_id.product_id.product_tmpl_id
                rec.variant_id = rec.lot_id.hifi_variant_id
```

- [ ] **Step 3: Commit**

```bash
git add repair_custom/models/repair_order.py
git commit -m "repair_custom: unlock lot_id for editing; drop serial_number sync in onchanges"
```

---

### Task 4: Rewrite `action_validate` to stop relying on `serial_number`

**Files:**
- Modify: `repair_custom/models/repair_order.py:878-932`

- [ ] **Step 1: Remove `action_generate_serial`**

Delete the method at lines 878-885 entirely. With `lot_id` editable and create-on-the-fly via Many2one, users type a serial into `lot_id`; the "generate" button is redundant and will be removed from the view in Task 6.

- [ ] **Step 2: Simplify `action_validate` lot-creation branch**

Edit lines 913-932.

Before (lot-creation branch only):
```python
        if self.product_tmpl_id and self.partner_id:
            # Create stock.lot for the device
            product = self.product_tmpl_id.product_variant_id
            if not product:
                raise UserError(_("Aucun produit trouvé pour cet appareil."))
            sn = self.serial_number or False
            lot_vals = {
                'name': sn or f"REP-{self.name}",
                'product_id': product.id,
                'company_id': self.company_id.id,
                'hifi_partner_id': self.partner_id.id,
            }
            if self.variant_id:
                lot_vals['hifi_variant_id'] = self.variant_id.id
            new_lot = self.env['stock.lot'].create(lot_vals)
            self.write({'lot_id': new_lot.id, 'serial_number': new_lot.name})
            # Seed quant at customer location and move to workshop
            Quant._update_available_quantity(product, customer_location, 1.0, lot_id=new_lot)
            self._create_repair_picking(customer_location, workshop_location)
        return self._action_repair_confirm()
```

After:
```python
        if self.product_tmpl_id and self.partner_id:
            # Fallback lot creation for programmatic callers / imports that
            # didn't set lot_id. Interactive users now set lot_id directly.
            product = self.product_tmpl_id.product_variant_id
            if not product:
                raise UserError(_("Aucun produit trouvé pour cet appareil."))
            lot_vals = {
                'name': f"REP-{self.name}",
                'product_id': product.id,
                'company_id': self.company_id.id,
                'hifi_partner_id': self.partner_id.id,
            }
            if self.variant_id:
                lot_vals['hifi_variant_id'] = self.variant_id.id
            new_lot = self.env['stock.lot'].create(lot_vals)
            self.write({'lot_id': new_lot.id})
            Quant._update_available_quantity(product, customer_location, 1.0, lot_id=new_lot)
            self._create_repair_picking(customer_location, workshop_location)
        return self._action_repair_confirm()
```

- [ ] **Step 3: Update constraint decorator at line 1035**

The constraint body (`_check_unit_consistency`) does not read `serial_number` — only the decorator references it. Edit line 1035.

Before:
```python
    @api.constrains('lot_id', 'product_tmpl_id', 'variant_id', 'serial_number')
```

After:
```python
    @api.constrains('lot_id', 'product_tmpl_id', 'variant_id')
```

- [ ] **Step 4: Commit**

```bash
git add repair_custom/models/repair_order.py
git commit -m "repair_custom: rewrite action_validate to drop serial_number usage"
```

---

### Task 5: Drop the `serial_number` field from the model

**Files:**
- Modify: `repair_custom/models/repair_order.py:391`

- [ ] **Step 1: Delete the field declaration**

Delete line 391:
```python
    serial_number = fields.Char("N° de série")
```

- [ ] **Step 2: Grep for any remaining usages in `repair_custom/` Python**

Run: `grep -rn "serial_number" /Users/martin/Documents/odoo_dev/custom_addons/repair_custom --include="*.py" | grep -v migrations | grep -v __pycache__`

Expected: only references in `wizard/hifi_inventory_wizard.py` (its own `serial_number` Char on the wizard — **do not touch**) and potentially `wizard/repair_pricing_wizard.py:202` and `models/repair_batch.py:294-296` — those will be fixed in Task 7.

- [ ] **Step 3: Commit**

```bash
git add repair_custom/models/repair_order.py
git commit -m "repair_custom: remove serial_number field from repair.order"
```

---

### Task 6: Update `repair_custom` views

**Files:**
- Modify: `repair_custom/views/repair_views.xml` (lines 393-405, 536-537, 872, 912)

- [ ] **Step 1: Replace intake block (form view, lines 393-405)**

Edit `repair_custom/views/repair_views.xml`.

Before:
```xml
                                    <label for="serial_number" string="N° de série"
                                        invisible="not product_tmpl_id"/>
                                    <div class="d-flex align-items-center gap-2"
                                        invisible="not product_tmpl_id">
                                        <field name="serial_number" nolabel="1"
                                            placeholder="Numéro de série"
                                            readonly="state not in 'draft' or lot_id"
                                            force_save="1"/>
                                        <button name="action_generate_serial" type="object"
                                            class="btn-sm" icon="fa-barcode"
                                            title="Générer N° Série"
                                            invisible="state != 'draft' or serial_number or lot_id"/>
                                    </div>
```

After:
```xml
                                    <label for="lot_id" string="Appareil physique / N° de série"
                                        invisible="not product_tmpl_id"/>
                                    <field name="lot_id" nolabel="1"
                                        invisible="not product_tmpl_id"
                                        readonly="state not in 'draft'"
                                        options="{'no_quick_create': True}"
                                        domain="[('product_id.product_tmpl_id', '=', product_tmpl_id),
                                                 ('is_hifi_unit', '=', True)]"
                                        context="{'default_product_id': product_tmpl_id and product_tmpl_id_variant_id_hint,
                                                  'default_hifi_partner_id': partner_id,
                                                  'default_hifi_variant_id': variant_id}"
                                        placeholder="Tapez le n° de série (existant ou nouveau)"/>
```

Note: `default_product_id` cannot reference a template directly — `stock.lot.product_id` is `product.product`. Two options:
  a. Add a helper `product_variant_id` related field on `repair.order` (related to `product_tmpl_id.product_variant_id`, store=False) and use it in the context.
  b. Handle the default via a Python `onchange`/`default_get` override on `stock.lot` when context has `default_product_tmpl_id`.

Pick (a). Add the helper field in Task 3.5 (see below) — **back-insert this as Task 3, Step 4 before moving on**. Revised context becomes:

```xml
                                        context="{'default_product_id': product_variant_id,
                                                  'default_hifi_partner_id': partner_id,
                                                  'default_hifi_variant_id': variant_id}"
```

- [ ] **Step 2: Back-insert helper field (retro-fix on Task 3)**

Open `repair_custom/models/repair_order.py` and add — right after the `lot_id` field declaration:

```python
    product_variant_id = fields.Many2one(
        'product.product',
        related='product_tmpl_id.product_variant_id',
        store=False, readonly=True,
        string="Variante produit (pour contexte lot)",
    )
```

- [ ] **Step 3: Replace search view references (lines 536-537)**

Before:
```xml
                            ('serial_number', 'ilike', self)]"/>
                <field name="serial_number"/>
```

After:
```xml
                            ('lot_id.name', 'ilike', self)]"/>
                <field name="lot_id" string="N° de série"/>
```

- [ ] **Step 4: Replace tree column (line 872)**

Before:
```xml
                <field name="serial_number" optional="show"/>
```

After:
```xml
                <field name="lot_id" string="N° de série" optional="show"/>
```

- [ ] **Step 5: Replace second search view (line 912)**

Same pattern as Step 3 — repoint `serial_number` to `lot_id.name`.

- [ ] **Step 6: Restart Odoo with update and load the repair form**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

Expected: clean upgrade. Then start Odoo normally and open a draft repair — confirm the intake block shows `lot_id` with autocomplete.

- [ ] **Step 7: Commit**

```bash
git add repair_custom/views/repair_views.xml repair_custom/models/repair_order.py
git commit -m "repair_custom: views use lot_id as intake; add product_variant_id helper"
```

---

### Task 7: Update wizards and batch model

**Files:**
- Modify: `repair_custom/wizard/repair_pricing_wizard.py:202`
- Modify: `repair_custom/models/repair_batch.py:294-296`

- [ ] **Step 1: Pricing wizard — swap serial lookup**

Read `repair_custom/wizard/repair_pricing_wizard.py` lines 195-210 first to see context.

Replace line 202 `sn = self.repair_id.serial_number` with:
```python
        sn = self.repair_id.lot_id.name or ''
```

- [ ] **Step 2: Batch model — swap serial in label**

Edit `repair_custom/models/repair_batch.py` lines 294-296.

Before:
```python
                if repair.serial_number:
                    label += _(" (S/N: %s)") % repair.serial_number
```

After:
```python
                if repair.lot_id:
                    label += _(" (S/N: %s)") % repair.lot_id.name
```

- [ ] **Step 3: Commit**

```bash
git add repair_custom/wizard/repair_pricing_wizard.py repair_custom/models/repair_batch.py
git commit -m "repair_custom: repoint wizard & batch label from serial_number to lot_id.name"
```

---

### Task 8: Update reports

**Files:**
- Modify: `repair_custom/report/repair_label.xml:160`
- Modify: `repair_custom/report/repair_ticket.xml:48`

- [ ] **Step 1: repair_label.xml**

Before:
```xml
                                                <span>S/N: </span><span t-field="o.serial_number"/>
```

After:
```xml
                                                <span>S/N: </span><span t-out="o.lot_id.name or ''"/>
```

- [ ] **Step 2: repair_ticket.xml**

Before:
```xml
                                <span style="font-size: 11px;">N° de série: <span t-field="repair.serial_number"/></span>
```

After:
```xml
                                <span style="font-size: 11px;">N° de série: <span t-out="repair.lot_id.name or ''"/></span>
```

- [ ] **Step 3: Upgrade module and render a ticket to verify**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

Then from the UI, print a ticket and label for an existing repair with a lot — verify serial renders.

- [ ] **Step 4: Commit**

```bash
git add repair_custom/report/repair_label.xml repair_custom/report/repair_ticket.xml
git commit -m "repair_custom: report templates read serial from lot_id.name"
```

---

### Task 9: Update `repair_appointment` module

**Files:**
- Modify: `repair_appointment/views/appointment_views.xml:81, 93, 106-107`
- Modify: `repair_appointment/tests/test_state_machine.py:122-123`

- [ ] **Step 1: Update appointment_views.xml**

For each occurrence of `<field name="serial_number"/>` at lines 81 and 93, replace with:
```xml
                                    <field name="lot_id" string="N° de série"/>
```

For the conditional block at lines 106-107:

Before:
```xml
                                                    <div t-if="record.serial_number.raw_value">
                                                        SN: <field name="serial_number"/>
```

After:
```xml
                                                    <div t-if="record.lot_id.raw_value">
                                                        SN: <field name="lot_id"/>
```

- [ ] **Step 2: Update test fixture writes**

Read `repair_appointment/tests/test_state_machine.py` lines 115-135 for context first.

Replace `batch.repair_ids[0].serial_number = 'SN123'` with lot-creation:
```python
        lot123 = self.env['stock.lot'].create({
            'name': 'SN123',
            'product_id': batch.repair_ids[0].product_tmpl_id.product_variant_id.id,
            'company_id': batch.repair_ids[0].company_id.id,
        })
        batch.repair_ids[0].lot_id = lot123
```

Same pattern for the `'SN456'` case on line 123.

- [ ] **Step 3: Run repair_appointment tests**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_appointment --test-tags=/repair_appointment --stop-after-init
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add repair_appointment/views/appointment_views.xml repair_appointment/tests/test_state_machine.py
git commit -m "repair_appointment: swap serial_number for lot_id in views and tests"
```

---

### Task 10: Update `repair_custom` tests

**Files:**
- Modify: `repair_custom/tests/test_quote_invoice_model.py:60-114`

- [ ] **Step 1: Read the test file to understand setup**

Run: `head -130 /Users/martin/Documents/odoo_dev/custom_addons/repair_custom/tests/test_quote_invoice_model.py`

Locate each assignment like `self.repair_a.serial_number = 'SN-AAA'` and each `'serial_number': 'SN-BBB'` in `create({...})` calls.

- [ ] **Step 2: Replace with lot creation + assignment**

Pattern A — replace:
```python
        self.repair_a.serial_number = 'SN-AAA'
```
with:
```python
        self.repair_a.lot_id = self.env['stock.lot'].create({
            'name': 'SN-AAA',
            'product_id': self.repair_a.product_tmpl_id.product_variant_id.id,
            'company_id': self.repair_a.company_id.id,
        })
```

Pattern B — replace `create({... 'serial_number': 'SN-BBB', ...})` with the repair creation *without* `serial_number`, then assign `lot_id` afterward (same creation pattern as above).

Apply to all four locations (lines 60, 67, 107, 114).

- [ ] **Step 3: Run the tests**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_custom --test-tags=/repair_custom:TestQuoteInvoiceModel --stop-after-init
```

Expected: all tests in this class pass.

- [ ] **Step 4: Commit**

```bash
git add repair_custom/tests/test_quote_invoice_model.py
git commit -m "repair_custom: tests use lot_id instead of removed serial_number"
```

---

### Task 11: Full-module sweep and smoke test

**Files:** (read-only verification)

- [ ] **Step 1: Final grep — no stray `serial_number` references on repair.order**

Run:
```bash
grep -rn "serial_number" /Users/martin/Documents/odoo_dev/custom_addons/ --include="*.py" --include="*.xml" | \
    grep -v __pycache__ | grep -v /migrations/ | grep -v hifi_inventory_wizard
```

Expected: only results should be inside `repair_devices/__init__.py` / `repair_devices/migrations/` (historical migration code referring to the *legacy `repair_device_unit.serial_number`* — a different model) — **do not touch those**.

- [ ] **Step 2: Full module reload**

```bash
cd /Users/martin/Documents/odoo_dev/odoo
./odoo-bin -c ../odoo.conf -u repair_custom,repair_appointment,repair_devices --stop-after-init
```

Expected: clean upgrade. Pre-migrate audit logs divergences (if any); post-migrate backfills draft lots; `serial_number` column dropped by ORM.

- [ ] **Step 3: Manual smoke test — follow spec § Verification pass**

Launch Odoo (not `--stop-after-init`) and run through:

1. Create a draft repair. Pick a product. Type an existing serial in `lot_id` → autocomplete suggests it.
2. Pick a new serial that doesn't exist → Many2one creates it on the fly. Open the lot's form (via external link) and verify product_id and partner were seeded correctly.
3. Rename the brand on the `product.template` used by this repair → reload the repair tree: device column shows new brand.
4. Rename the `stock.lot.name` → reload the repair tree: serial column shows new name.
5. Move the product to a different HiFi category → `is_hifi_device` stays true, repair form still shows the device.
6. Print a ticket and a label → serial renders from `lot_id.name`.
7. Confirm a new repair (`action_validate`) without touching the intake fields beyond product + lot → picking is created, quant seeded, no traceback.

- [ ] **Step 4: Commit smoke-test notes (optional, only if findings)**

If smoke test surfaces any issue, fix + commit. Otherwise no commit for this step.

---

### Task 12: Push branch

- [ ] **Step 1: Push**

```bash
git push -u origin feature/device-lot-edit-propagation
```

- [ ] **Step 2: Open PR** (when user requests)

Command available on demand; not auto-run.

---

## Notes for the implementer

- **Odoo tests are module-install tests**, not pytest. Run them via `--test-tags`. If `--test-tags` syntax differs on your Odoo 17 build, use `--test-enable -u <module>` for the full suite.
- **The `hifi_inventory_wizard` has its own `serial_number` Char** — that's a wizard-local field, unrelated to `repair.order.serial_number`. Leave it alone.
- **`repair_devices/__init__.py` and `repair_devices/migrations/2.7/` reference a legacy `repair_device_unit.serial_number`** from a prior model that no longer exists in the live schema. Those are frozen historical migration artifacts — leave them alone.
- If Odoo refuses to drop the `serial_number` column because something still references it in XML that hasn't been loaded yet, rerun with `-u repair_custom,repair_appointment` together.
