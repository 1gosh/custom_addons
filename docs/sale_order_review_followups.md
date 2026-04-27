# Sale-order extensions — deferred follow-ups

Backlog from the in-depth review of the sale.order customisations
(`repair_custom/models/repair_extensions.py` and friends).

Items already shipped on branch `sale-order-cancel-and-cleanup`:
- **B** — `_action_cancel` override to roll back equipment-sale lot stamps
  and rental internal transfers.
- **N** — minor cleanups (`_get_warehouse()` helper, `_make_rental_transfer()`
  factor, `move_fields` rename, explicit return in `_action_launch_stock_rule`).

Everything below is **not yet implemented**. Order roughly reflects priority.

---

## C. Replace hand-rolled rental transfers with a stock.route

**Where it bites today**

`SaleOrder.action_confirm`, `action_return_rental`, and `_action_cancel`
all build internal transfers between `WH/Stock` and `Appareils en Location`
manually, via `_make_rental_transfer`. The factor in N reduced duplication
but the architectural debt remains: rentals don't go through Odoo's
procurement at all (`SaleOrderLine._action_launch_stock_rule` short-circuits
for rentals), so there's no rule-based control point for routing, lead times,
or replenishment behaviour.

**Target shape**

1. Define a `stock.route` "Location" on the rental sale.order.template
   (or via a route applied through a "rental" warehouse picking type).
2. Two pull rules:
   - Stock → Rented (triggered by SO confirm)
   - Rented → Stock (triggered by a return picking, created from the
     stock-return wizard or a dedicated button)
3. Drop `_make_rental_transfer`, drop the rental short-circuit in
   `_action_launch_stock_rule`, drop the manual `stock.picking` creation
   in `action_confirm` / `action_return_rental` / `_action_cancel`.
4. Keep `rental_state` and `action_return_rental` as the user-facing
   driver — but their job becomes "trigger the route" rather than
   "build pickings".

**Risks / things to verify**

- The standard `_action_launch_stock_rule` will fire on every order line
  (HiFi *and* accessories). Make sure non-HiFi rental lines (consumable
  rentals, if any) don't try to reserve serial-tracked stock.
- Reservations against the Rented location must not feed replenishment.
  Confirm the route doesn't put pressure on reorder rules — the Rented
  location is currently parented to `stock.stock_location_locations`
  (outside any warehouse), which is the right behaviour to preserve.
- Cancel rollback (issue B) must be reworked at the same time: today
  `_action_cancel` short-circuits the picking; with routes, cancelling
  the SO should cancel the not-yet-validated outgoing picking, and a
  validated outgoing picking needs an explicit return.

**Estimate:** ~1 day, mostly setup data + tests.

---

## D. Idempotency guard on rental `action_confirm`

**Where it bites today**

If `action_confirm` runs twice on a rental SO (concurrent click, retry
after a partial exception, scripted flow), the override re-creates a
second internal transfer Stock → Rented. The second transfer fails with
"Stock insuffisant" because the unit is already in Rented, but a stray
`stock.picking` row is left behind that someone has to clean up by hand.

**Fix sketch**

In `SaleOrder.action_confirm`, before invoking `_make_rental_transfer`
for a rental, short-circuit when the work is already done:

```python
if order.rental_state == 'active':
    continue  # already confirmed
already = order.picking_ids.filtered(
    lambda p: p.location_dest_id == rented_location
              and p.state not in ('cancel',)
)
if already:
    continue
```

A targeted regression test: confirm a rental SO twice in a row, assert
exactly one outgoing picking exists.

This becomes obsolete if C (route-based) lands first.

**Estimate:** ~30 min, including a test.

---

## F. `_compute_tax_id` stomps on fiscal_position output

**Where it bites today**

`SaleOrderLine._compute_tax_id` (`repair_extensions.py`) calls `super()`
then unconditionally overwrites `tax_id` for repair_quote, rental, and
equipment_sale lines. Two consequences:

1. A user (or an `account.fiscal.position` mapping) that legitimately
   wanted a different tax — e.g. an EU intra-com VAT-exempt shipment, or
   an export — gets it silently overwritten.
2. A user who hand-edits a line's `tax_id` will see it reverted on the
   next compute trigger (changing `product_id` or `lot_id`).

Today this is fine because all customers are French and on the standard
20% VAT path, but it's a landmine the moment the shop ships once outside
the country.

**Fix sketch**

Two options, in order of preference:

1. **Hook into the fiscal_position layer.** Move the rules into
   `account.fiscal.position` (or an inherit of `_get_taxes_for_fiscal_position`)
   so fiscal positions cascade naturally and standard Odoo rules win when
   they should.
2. **Defensive override.** Only overwrite when the current `tax_id` is
   unset *or* matches whatever super() just produced (i.e. the user/FP
   hasn't customised it). Snapshot the post-super value before
   overwriting:

   ```python
   def _compute_tax_id(self):
       super()._compute_tax_id()
       for line in self:
           # only override if the user/FP didn't customise
           default_tax = line.product_id.taxes_id  # or super()'s logic
           if line.tax_id and line.tax_id != default_tax:
               continue
           ...
   ```

**Tests to add**

- Equipment sale line with a manually-set fiscal position → manual taxes
  win.
- Repair quote line with a hand-edited `tax_id` → not reverted on next
  product change.

**Estimate:** ~2 hours including tests.

---

## G. Replace `restrict_lot_id` shim with OCA `sale_stock_restrict_lot`

**Where it bites today**

`SaleOrderLine._prepare_procurement_values`, `StockRule._get_custom_move_fields`,
and `StockMove._update_reserved_quantity` / `_prepare_move_line_vals`
collectively re-implement what the OCA module
[`sale_stock_restrict_lot`](https://github.com/OCA/stock-logistics-warehouse)
already provides. Per CLAUDE.md the project prefers OCA where available.

**Fix sketch**

1. Add `sale_stock_restrict_lot` to the OCA pin set / requirements.
2. Add it as a dependency of `repair_custom`'s manifest.
3. Delete the four custom snippets:
   - `SaleOrderLine._prepare_procurement_values` (the `restrict_lot_id` line)
   - `SaleOrderLine.lot_id` field — keep ours if OCA's name differs;
     reconcile naming with whatever OCA exposes.
   - `StockRule._get_custom_move_fields`
   - `StockMove.restrict_lot_id` + the two move overrides
4. Run a one-shot data migration that copies any existing
   `sale.order.line.lot_id` and `stock.move.restrict_lot_id` rows over
   to the OCA equivalents if their column names differ.

**Risks**

- OCA's lot-restriction semantics might apply at a different point in
  the reservation pipeline; the rental flow (which currently passes
  `lot_ids` directly on manual move creation) may bypass it entirely
  and stop working. Test cancel + re-confirm cycles, partial deliveries,
  and backorders.
- Field name collision (`restrict_lot_id` is the OCA convention too —
  good — but verify ondelete behaviour).

**Estimate:** ~half a day, dominated by migration and regression test
coverage.

---

## H. `_seed_hifi_quants` inner-loop misleading

**Status:** mostly cosmetic, low priority.

`_seed_hifi_quants` is invoked as `self._seed_hifi_quants()` with `self`
already a singleton inside `action_confirm`'s outer `for order in self`
loop. The inner `for order in self` then iterates a recordset of one,
which is harmless but confusing.

**Fix**

Either:
- Remove the inner loop and add `self.ensure_one()`, since the helper
  is always called per-record.
- Or keep the recordset semantics and call it as `self._seed_hifi_quants()`
  *outside* the outer loop in `action_confirm` (i.e. seed in batch).

The second form is slightly more efficient (one warehouse lookup per
recordset rather than per record) — go with it.

**Estimate:** ~10 min.

This issue is partially eclipsed by the bigger question of whether
`_seed_hifi_quants` should exist at all (see review issue **E**: it can
mint stock from thin air, and once the lot-picker domain is fixed in
issue **A** it likely becomes dead code).

---

## I. Verify `stock_state` recompute on quant changes

**Status:** verification task, possibly a no-op fix.

`StockLot.stock_state` is stored, with depends
`('location_id', 'functional_state', 'sale_order_id')`. In Odoo 17,
`stock.lot.location_id` is itself a stored compute on
`('quant_ids.location_id', 'quant_ids.quantity')`, so a quant change
*should* transitively invalidate `stock_state`.

**Action**

1. Walk through a full rental cycle in a test DB:
   - Confirm → unit moves Stock → Rented → assert `stock_state == 'rented'`.
   - Return → unit moves Rented → Stock → assert `stock_state == 'stock'`.
   - Cancel-from-active → assert `stock_state == 'stock'` again.
2. If any step fails to update without a manual recompute, broaden the
   depends:
   ```python
   @api.depends(
       'location_id', 'location_id.usage',
       'quant_ids.location_id', 'quant_ids.quantity',
       'functional_state', 'sale_order_id',
   )
   def _compute_stock_state(self):
       ...
   ```

**Estimate:** ~30 min if the chain works; ~1 hour if recomputes need
broadening.

---

## J. `stock.lot.name_create` too narrow

**Where it bites today**

`StockLot.name_create` only creates a HiFi lot when `default_product_id`
is in context. From the SO line picker the context is
`{'default_product_id': product_id}`, but if the user types a new SN in
the picker *before* picking a product, `default_product_id` isn't yet
set, super() runs, and the user gets the standard Odoo error.

**Fix sketch**

- In the SO line picker: forbid typing a new SN while `product_id` is
  empty (the existing UX already pushes you to pick a product first;
  enforce by setting `readonly` on the lot picker until product is set).
- Or: in `name_create`, look up the product from the typed SN against
  recent lots / the product on the line via a custom RPC. This is
  fragile.

The first option is the right call.

**Estimate:** ~15 min view tweak + 1 test.

---

## L. Tests for sale_order extensions (equipment_sale + rental)

**Where it bites today**

`repair_custom/tests/` covers `quote_lifecycle`, `quote_invoice_model`,
`completion_pickup` — all repair-quote scoped. Nothing exercises:

- equipment_sale: confirm stamps the lot (`sav_expiry`, `sale_date`,
  `sale_order_id`, `hifi_partner_id`), invoice triggers no delivery
  wizard, margin tax (0%) is applied to HiFi lines, auto-validate runs.
- rental: confirm creates the Stock → Rented transfer, return creates
  the inverse, cron flips overdue when the end date passes,
  start/end-date validation rejects invalid input, no double-confirm
  side effects.
- restrict_lot_id: a sold/rented unit reserves the exact serial.
- **B (new):** cancelling an equipment sale clears the lot's SAV
  fields; cancelling an active rental moves the unit back to Stock and
  resets `rental_state` to `draft`; cancelling an already-returned
  rental does nothing destructive.

**Coverage targets**

Roughly 1 test class per top-level scenario. Reuse the existing
`tests/common.py` fixtures. Aim for ~15–20 assertions total.

CLAUDE.md flags production data integrity as a constraint, so this is
the highest-leverage debt item on this list once the structural fixes
(B, C, A from the review) have landed.

**Estimate:** ~1 day for full coverage.

---

## Cross-cutting note

Issues C (rental routes) and B (cancel rollback) interact: if you ship C
first, the cancel-rollback in B becomes simpler — Odoo's standard cancel
on a route-driven picking does most of the work. Worth re-reading B's
implementation when C is being designed and possibly simplifying it
then.
