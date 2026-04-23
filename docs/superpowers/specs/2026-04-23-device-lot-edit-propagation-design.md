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

### 1. Remove the redundant `serial_number` Char

- **Delete** `repair.order.serial_number = fields.Char(...)` at `repair_order.py:391`.
- Replace every view/report reference to `serial_number` with `lot_id.name` (or `lot_id` using the lot's live `display_name`).
- For inline editing of the serial from the repair form, rely on the `external_link` on the `lot_id` Many2one widget — users click it, edit `name` on `stock.lot`, and every repair referencing that lot updates instantly (lot's `display_name` is non-stored).

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

`repair.order.serial_number` is being dropped. If any existing rows have a `serial_number` value that differs from `lot_id.name` (because the serial was edited on the lot after the repair was cached), we prefer the lot's current value — no data migration needed. The column can be dropped by Odoo's ORM on module update; for a paranoid audit, run a pre-upgrade SQL check:

```sql
SELECT id, name, serial_number, lot_id
FROM repair_order
WHERE serial_number IS NOT NULL
  AND serial_number != COALESCE((SELECT sl.name FROM stock_lot sl WHERE sl.id = lot_id), '');
```

Any rows returned are candidates for manual review before the module update.

## Risks

- **Non-stored `device_id_name` performance.** For the current volume (repairs in the thousands, not millions), the cost of recomputing per row is negligible. Flag only if list rendering shows latency.
- **External code reading `serial_number`.** The grep pass + a module `-u` reload will catch this — Odoo will raise on missing field references at load time.
