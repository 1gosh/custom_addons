# Category Short Name (Abbreviation) — Design

## Goal

Allow users to set an abbreviation per `product.category` and use these abbreviations (rather than the full category names) on the repair label, while still showing the last two levels of hierarchy.

## Current behavior

`repair_custom/models/repair_order.py:354` defines `category_short_name` as a computed field. It splits `category_id.complete_name` on `" / "` and returns the last two segments joined by `" / "`. The repair label (`repair_custom/report/repair_label.xml:194`) reads this field.

## Target behavior

Walk up the category tree from `category_id` and collect, in top-down order, the last two categories (parent + self; or just self when there is no parent). For each collected category, display `short_name` if set, otherwise `name`. Join with `" / "`.

Example: category path `Audio / Amplificateurs / Préamplificateurs` where Amplificateurs.short_name = "AMP" and Préamplificateurs.short_name = "Preamp" → `AMP / Preamp`. If only Préamplificateurs has a short_name → `Amplificateurs / Preamp`.

## Changes

1. **`repair_devices/models/product_category.py`** (new) — inherit `product.category` and add `short_name = fields.Char(string="Abréviation")`. Register the new file in `repair_devices/models/__init__.py`.

2. **`repair_devices/views/`** — extend the standard `product.category` form view to add the `short_name` field (near `name`). Register the new view in `repair_devices/__manifest__.py` data.

3. **`repair_custom/models/repair_order.py`** — rewrite `_compute_category_short_name` to walk `category_id.parent_id` instead of splitting `complete_name`, using `short_name or name` per segment. Update `@api.depends` to track `category_id.parent_id`, `category_id.short_name`, `category_id.name`, `category_id.parent_id.short_name`, `category_id.parent_id.name`.

4. **No change to `repair_label.xml`** — it continues reading `o.category_short_name`.

## Module placement

The field lives in `repair_devices` (the module that owns device categorization). The compute that consumes it stays in `repair_custom` where the repair order and its label live. `repair_custom` already depends on `repair_devices` (per `__manifest__.py`), so the compute can reference the new field safely.

## Out of scope

- No data migration: existing categories simply have `short_name` empty and fall back to `name` on the label until users fill abbreviations in.
- No changes to search, tree views, or reports other than the repair label.
