# Device / Lot Edit Propagation — Design

**Date:** 2026-04-23
**Branch:** `feature/device-lot-edit-propagation`
**Scope:** `repair_custom`, `repair_devices`

## Problem

When creating repair orders, users make entry mistakes on:

1. **Model name typos** on `product.template.name`
2. **Wrong brand** on `product.template.brand_id` (e.g. auto-brand-detection picked the wrong one)
3. **Wrong category** on `product.template.categ_id`
4. **Serial typos** on `stock.lot.name`

Today, edits to these fields don't always propagate to what the repair order displays because:

- `repair.order.device_id_name` is a **stored** computed field that depends on `product.template.display_name` (a **non-stored** computed). This chain is fragile — stored fields caching non-stored computes is a well-known Odoo footgun and a prime source of stale labels.
- `repair.order.serial_number` is a legacy free-text `Char` that duplicates `stock.lot.name`. Once set, it never re-reads the lot — any rename of the serial number leaves the repair showing the old value.
- There is no in-form edit path: correcting a brand/model/category/serial requires navigating away from the repair.

## Goals

- Edits to `product.template` (brand, model name, category) and `stock.lot.name` propagate to every repair view without manual recompute.
- Fixing a typo or wrong brand/category takes a single click from the repair form.
- No duplicate/cached copies of device or serial strings on `repair.order`.

## Non-goals

- Merging duplicate `product.template` records (scenario D).
- Reassigning a repair to a different lot (scenario E).
- Restricting who can edit `product.template` / `stock.lot` (permissions already in place).

## Design

### 1. Replace `serial_number` Char with a Many2one on `stock.lot`

The `serial_number` Char exists today so users can type a customer's serial at intake; the lot + quant are materialized at confirmation. We keep that purpose but change the field type so that:

- typed input autocompletes against existing lots (duplicate detection while typing);
- the repair always points at the real `stock.lot` record (no drift);
- warranty / picking logic — which already hangs off `lot_id` — is untouched.

**Changes on `repair.order`:**

- **Delete** `serial_number = fields.Char(...)` at `repair_order.py:391`.
- **Promote `lot_id`** from `readonly=True` (currently set at confirmation) to an editable Many2one used as the intake field itself. Remove the `readonly=True` constraint, but keep it disabled until `product_tmpl_id` is set (`readonly="not product_tmpl_id"` in the view).
- Autocomplete is **scoped to the currently selected product**: apply
  `domain="[('product_id.product_tmpl_id', '=', product_tmpl_id)]"`
  on the `lot_id` field in the repair form. This keeps the dropdown focused on the right model and still surfaces existing duplicates for that model as the user types.
- **Create-on-the-fly at entry.** Allow Many2one's default `name_create` path: when the user types a serial that doesn't exist, Odoo creates a new `stock.lot` immediately. We need to ensure `product_id` is supplied at creation — do this by adding `context="{'default_product_id': product_variant_id}"` (or equivalent) on the field so `name_create` produces a valid lot. Lots without quants are harmless; the quant/incoming-stock transfer continues to happen at repair confirmation as it does today. Repairs cancelled before confirmation may leave orphan lots — acceptable, and cleanable via a periodic query if it becomes a concern.
- If the currently-selected `product_tmpl_id` changes after a lot was picked, clear `lot_id` (existing `onchange` at `repair_order.py:416` already does something similar — extend/verify).

**Views / reports:**

- Replace every view/report reference to `serial_number` with `lot_id` (live `display_name`) or `lot_id.name` where only the raw serial is needed.
- Ensure the external-link arrow on the `lot_id` Many2one is visible so the user can jump to the lot form to fix a typo post-creation.

### 2. Make `device_id_name` non-stored

Keep the field as a single canonical label for views (handy for activity cards, tree columns, kanban), but **remove `store=True`**.

```python
device_id_name = fields.Char("Appareil", compute="_compute_device_id_name")  # not stored
```

The compute stays essentially as-is (`repair_order.py:397-412`): lot's product display_name when a lot is attached (so variant and live brand/model are included), else `product_tmpl_id.display_name` + optional `variant_id.name` suffix, else `"Aucun modèle"`.

**Trade-off accepted:** we lose direct search/sort on `device_id_name`. Mitigation: tree/search views already expose `product_tmpl_id` (which has a custom `_name_search` matching brand+model terms) and `lot_id` — both live and searchable.

### 3. Make edits reachable from the repair form

- Add `options="{'no_create': True, 'no_create_edit': True}"` is already fine — what we want is to ensure the **open arrow** (external link) is visible on:
  - `product_tmpl_id` Many2one widget
  - `lot_id` Many2one widget
- This is on by default for Many2one but sometimes suppressed by `options`. Audit the form view and ensure the external link renders for both fields.

### 4. Verification pass

After the changes, perform a manual smoke test:

1. Create/open a repair with brand "B&O", model "Beogram 3000", serial "SN-123".
2. Rename the brand → confirm tree, kanban, activity card, and form all show the new brand.
3. Rename the product's model `name` → same confirmation.
4. Move the product to a different (still HiFi) category → `is_hifi_device` stays true, display still correct.
5. Rename the stock.lot serial → confirm any view that surfaces `lot_id` / `lot_id.name` shows the new serial.
6. Confirm no view or report still references the removed `serial_number` field (grep + module install test).

## Files to touch

- `repair_custom/models/repair_order.py` — remove `serial_number` field, remove `store=True` from `device_id_name`, clean up compute deps if over-specified.
- `repair_custom/views/repair_views.xml` — remove `serial_number` references; ensure `product_tmpl_id` and `lot_id` open arrows are visible.
- `repair_custom/views/*.xml` (search all) — same cleanup.
- `repair_custom/report/*.xml` — check templates for `serial_number` usage.
- Any controller or wizard referencing `serial_number` (e.g. `repair_tracking.py`, `repair_pricing_wizard.py`) — repoint to `lot_id.name`.

## Migration

`repair.order.serial_number` is being dropped. Because we're also changing how `lot_id` is populated (now at entry rather than at confirmation), existing data needs a one-shot backfill:

1. **Audit step — find rows where the cached serial and the lot disagree:**

   ```sql
   SELECT id, name, serial_number, lot_id
   FROM repair_order
   WHERE serial_number IS NOT NULL
     AND serial_number != COALESCE((SELECT sl.name FROM stock_lot sl WHERE sl.id = lot_id), '');
   ```

   Any rows returned are candidates for manual review before the module update — the truth is almost always `lot.name`, but we want eyes on it.

2. **Backfill step — for draft repairs with a typed `serial_number` but no `lot_id`:** create matching `stock.lot` records (scoped to their `product_variant_id`) or link to an existing lot if one with the same `(serial, product)` tuple already exists. This can run as a post-install migration hook.

3. After backfill, Odoo's ORM drops the `serial_number` column on module update.

## Risks

- **Non-stored `device_id_name` performance.** For the current volume (repairs in the thousands, not millions), the cost of recomputing per row is negligible. Flag only if list rendering shows latency.
- **External code reading `serial_number`.** The grep pass + a module `-u` reload will catch this — Odoo will raise on missing field references at load time.
