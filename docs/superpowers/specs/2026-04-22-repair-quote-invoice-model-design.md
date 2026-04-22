# Repair Quote & Invoice Model Rework — Theme A — Design Spec

**Date:** 2026-04-22
**Target module:** `repair_custom` (extension, no new addon)
**Depends on:** `repair_custom`, `sale_management`, `account`
**Branch:** `feature/repair-quote-invoice-model` (from `main`)

## Context and scope

Theme B (see `2026-04-22-repair-batch-ux-polish-design.md`) polished the batch/repair UX without touching the quote or invoice model. Theme A is the model rework. It inverts a load-bearing decision from sub-project 2 (`2026-04-11-repair-quote-lifecycle-design.md` decision #11: "one batch = one sale.order = one quote decision") and reshapes the invoicing entry points.

Four drivers:

1. **Quotes per repair, never grouped.** Each repair in a batch has its own quote lifecycle. The pricing wizard's batch-mode walkthrough (Appareil k/N, accumulated lines) is dead — a repair whose siblings are still in `quote_state='none'` must not block quoting.
2. **Invoicing from existing quotes.** When a repair has an approved quote, the invoice must originate from that quote — the pricing wizard's invoice mode is killed. Every `account.move` in the repair flow is born from a `sale.order`.
3. **Batch-level consolidation.** The manager needs to fold all accepted quotes of a batch into one `account.move` with a single click (PDF segmented by device).
4. **Native `sale.order` "Créer la facture" preservation.** Going through the native button today loses the `repair_id` / `batch_id` stamps on the resulting move. The Theme B pickup wizard's `account.move.action_post` hook still fires via the `sale_line → repair_order_ids` fallback, but per-repair metadata for reports, filters, and the `repair.invoice_ids` smart link is missing.
5. **Partial acceptance.** A deposit with N quotes where only some are accepted must deliver cleanly: accepted repairs get invoiced + SAR + delivered; refused repairs get handed back un-repaired at the same pickup; the batch still reaches `delivery_state='delivered'`.

### Out of scope

- No post-migration split of legacy `sale.order` records that point to N repairs. Historical shape is honored; new code is 1:1 and does not retro-fit.
- No new mail templates, no new activity types, no new CRON, no new portal routes. Pure code/view change.
- No refusal-handling UI beyond what sub-project 2 already ships ("statuer" activity). The manager's abandon/re-quote decisions stay manual.
- No change to the per-repair `quote_state` machine or the `_apply_quote_state_transition` entry point from sub-project 2.

## Decisions captured from brainstorming

| # | Decision | Why |
|---|---|---|
| 1 | One `sale.order` per repair, always. Batch-mode walkthrough removed from the pricing wizard | Workflow is processed per repair anyway. Siblings in `quote_state='none'` must not block quoting. Partial acceptance becomes natural (N quotes, each with its own state) |
| 2 | `repair.order.sale_order_id` stays Many2one; `sale.order.repair_order_ids` stays One2many but effectively 1:1 going forward | Keeps the ORM shape stable for legacy data (decision #7). New code assumes 1:1 freely |
| 3 | "Facturer le devis accepté" button on both `repair.order` form (singleton SO) and `repair.batch` form (all eligible SOs consolidated into one move) | User explicitly requested both surfaces. Both route to the same helper — repair-form button is just the singleton case |
| 4 | Consolidation mechanics: native `(so1+so2+…)._create_invoices()` + post-processing to inject `display_type='line_section'` headers per repair | Native path preserves `sale.line ↔ invoice.line` links (feeds the existing `action_post` fallback). Post-processing preserves the per-device visual segmentation that today's wizard produces |
| 5 | Native `sale.order` "Créer la facture" button is **hidden** on repair quotes (`computed_order_type == 'repair_quote'`) and replaced by a repair-aware button | Keeps one visible path on the SO form. Per-SO button invoices only that SO (C.1 — no auto-promote to batch consolidation); batch consolidation is reached via the batch form |
| 6 | `account.move` auto-stamps `repair_id` / `batch_id` on create by resolving `invoice_line_ids.sale_line_ids.order_id.repair_order_ids` | Defensive net: any path (list-view bulk invoice, scripted creation, future surfaces) that produces a repair-linked move gets the metadata populated. Our button becomes idempotent w.r.t. the auto-stamp |
| 7 | Check for "this is a repair quote" uses `computed_order_type == 'repair_quote'` (template-derived, already stored), not `repair_order_ids` | Intent-bearing: the document type is the signal, not the link. The field is stored+indexed; equipment sales and rentals are unaffected |
| 8 | Pricing wizard becomes quote-only. `generation_type` field, `_create_global_invoice`, and related branches deleted. Walk-in invoices require three clicks: create quote → confirm SO → facturer le devis. **The wizard entry button stays available throughout the repair lifecycle (any non-draft state), independent of `quote_state`** — `quote_state='pending'` is the technician→manager request-for-quote signal, not a precondition for the manager to produce a document | Every repair `account.move` is born from an approved `sale.order`. Unifies every downstream hook (`action_post`, reports, filters). Walk-in flow (client doesn't care about seeing a quote): manager opens the wizard directly without waiting on the technician's "Établir devis" button |
| 9 | Partial acceptance: refused-quote repairs eligible for the batch `Livrer` button regardless of `repair.state`. Silent side effect: delivering a refused-quote repair sets `repair.state='cancel'` (cleanup only; no new UI) | Matches real-world pickup ("client takes everything at once, pays for what got fixed"). Consolidation helper naturally excludes refused repairs (no approved quote → no sale.line → no contribution) |
| 10 | No post-migration for legacy multi-repair sale.orders. New code treats them as a single contribution; section headers are injected per SO rather than per repair when the legacy shape is detected | Legacy SOs are mostly terminal (invoiced or cancelled). Splitting them would fabricate accounting history. Forward-only discipline |
| 11 | Eligibility predicate: `quote_state == 'approved'` AND `sale_order_id.invoice_status in ('to invoice', 'upselling')`. `repair.state` does not gate invoicing | Approved = manager+client both consented. Invoicing deposits/advances on a not-yet-done repair is the manager's call |

## Architecture

All changes live inside `repair_custom`. No new XML data files. One view inheritance on `sale.order`. One new batch-level helper shared by two button entry points. One `account.move` create-override for auto-stamping. Pricing wizard is trimmed down.

### Module layout (delta)

```
repair_custom/
├── __manifest__.py                    # MODIFIED: version bump 17.0.1.7.0
├── models/
│   ├── repair_order.py                # MODIFIED:
│   │                                  #   - action_open_pricing_wizard removed
│   │                                  #   - action_invoice_repair_quote added
│   │                                  #   - is_quote_invoiceable computed field
│   ├── repair_batch.py                # MODIFIED:
│   │                                  #   - action_invoice_approved_quotes added
│   │                                  #   - _invoice_approved_quotes(repairs) core helper
│   │                                  #   - _inject_repair_section_headers(move)
│   │                                  #   - has_invoiceable_quotes computed field
│   │                                  #   - action_mark_delivered: refused-quote eligibility
│   │                                  #     + state='cancel' side effect
│   └── repair_extensions.py           # MODIFIED:
│                                      #   - AccountMove.create() override (auto-stamp)
├── wizard/
│   └── repair_pricing_wizard.py       # MODIFIED (heavy trim):
│                                      #   - generation_type field removed
│                                      #   - batch_id / remaining_repair_ids /
│                                      #     accumulated_lines_json / step_info removed
│                                      #   - action_next_step removed
│                                      #   - _create_global_invoice removed
│                                      #   - default_get simplified (CAS A gone)
│                                      #   - action_confirm short to quote creation only
├── views/
│   ├── repair_views.xml               # MODIFIED:
│   │                                  #   - "Devis/Facture" button → "Devis" (quote-only entry)
│   │                                  #   - "Facturer le devis" button added, bound to
│   │                                  #     is_quote_invoiceable
│   ├── repair_batch_views.xml         # MODIFIED:
│   │                                  #   - "Facturer les devis acceptés" header button
│   ├── repair_pricing_wizard_views.xml# MODIFIED:
│   │                                  #   - generation_type field + all invoice-mode xpaths removed
│   └── sale_order_views.xml           # NEW:
│                                      #   - inherit form; hide action_create_invoice;
│                                      #     add action_invoice_repair_quote
└── tests/
    └── test_quote_invoice_model.py    # NEW
```

No schema migration. `account.move.repair_id` and `batch_id` fields already exist (sub-project 3).

## Section 1 — Pricing wizard becomes quote-only

### Removed

- `generation_type` field (and its radio in the form view). Wizard only creates quotes.
- `batch_id`, `remaining_repair_ids`, `accumulated_lines_json`, `step_info` — batch walkthrough is dead.
- `action_next_step` — no multi-step loop.
- `_create_global_invoice` and its callers.
- `default_get` branch "CAS A : On vient d'un BATCH". The `active_model == 'repair.batch'` context path is removed.
- Any batch-form XML entry point that passes `active_model='repair.batch'` context to the wizard.
- `repair.order.action_open_pricing_wizard` (generic/bimodal entry point).

### Kept / simplified

- `repair_id` (required), `invoice_template_id`, `use_template`, `target_total_amount`, `extra_parts_ids`, `parts_mode`, `manual_label`, `manual_product_id`, `add_work_details`, `work_details`.
- `default_get` keeps only "CAS B : On vient d'une réparation UNIQUE".
- `action_confirm` becomes:

```python
def action_confirm(self):
    self.ensure_one()
    lines = self._get_invoice_lines_formatted()
    try:
        with self.env.cr.savepoint():
            return self._create_quote(lines)
    except Exception as e:
        _logger.error("Failed to create quote: %s", e)
        raise UserError(_("Erreur lors de la création du devis : %s") % e)

def _create_quote(self, lines):
    if self.repair_id.sale_order_id:
        raise UserError(_("Un devis est déjà lié à cette réparation."))
    # ... (lines assembly identical to today's _create_global_sale_order,
    #      minus the batch_id branch — always singleton)
```

### Entry point

Repair form button "Devis" (renamed from "Devis/Facture") calls `action_create_quotation_wizard`, which passes `default_repair_id=self.id` — unchanged behavior minus the `default_generation_type='quote'` context key (no longer needed; wizard is quote-only).

**Visibility:** the button stays available for any non-draft repair, regardless of `quote_state`. Hidden only when `state == 'draft'` (existing rule) or `sale_order_id` already exists (wizard would reject anyway — hide rather than let the user hit the error). This preserves the walk-in flow: a manager who needs to produce a document without waiting on the technician's "Établir devis" signal can open the wizard at any time.

```xml
<button name="action_create_quotation_wizard"
        icon="fa-file" type="object" string="Devis"
        invisible="state == 'draft' or sale_order_id"/>
```

The technician's "Établir devis" button (`action_atelier_request_quote`) remains the separate diagnostic-request path: it writes `quote_state='pending'`, posts chatter, and lands the repair in the manager's queue. It is NOT a precondition for running the wizard.

## Section 2 — "Facturer le devis" flow

### `repair.order` side

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
            and rec.sale_order_id
            and rec.sale_order_id.invoice_status in ('to invoice', 'upselling')
        )

def action_invoice_repair_quote(self):
    self.ensure_one()
    return self.batch_id._invoice_approved_quotes(self)
```

Form button:

```xml
<button name="action_invoice_repair_quote"
        type="object"
        string="Facturer le devis"
        class="btn-primary"
        invisible="not is_quote_invoiceable"/>
```

### `repair.batch` side

```python
has_invoiceable_quotes = fields.Boolean(
    compute='_compute_has_invoiceable_quotes',
    store=False,
)

@api.depends('repair_ids.is_quote_invoiceable')
def _compute_has_invoiceable_quotes(self):
    for batch in self:
        batch.has_invoiceable_quotes = any(r.is_quote_invoiceable for r in batch.repair_ids)

def action_invoice_approved_quotes(self):
    self.ensure_one()
    eligible = self.repair_ids.filtered('is_quote_invoiceable')
    if not eligible:
        raise UserError(_("Aucun devis accepté à facturer dans ce dossier."))
    return self._invoice_approved_quotes(eligible)

def _invoice_approved_quotes(self, repairs):
    """Core helper shared by repair-form and batch-form buttons."""
    self.ensure_one()
    if not repairs:
        raise UserError(_("Aucune réparation sélectionnée."))
    sale_orders = repairs.mapped('sale_order_id')
    if not sale_orders:
        raise UserError(_("Aucun devis lié aux réparations sélectionnées."))
    moves = sale_orders._create_invoices()  # native: one consolidated draft for same partner
    for move in moves:
        self._inject_repair_section_headers(move)
        if not move.batch_id:
            move.batch_id = self.id
        # repair_id auto-stamped by account.move.create override when unique
    return {
        'name': _("Facture Générée"),
        'type': 'ir.actions.act_window',
        'res_model': 'account.move',
        'res_id': moves[:1].id if len(moves) == 1 else False,
        'view_mode': 'form' if len(moves) == 1 else 'tree,form',
        'domain': [('id', 'in', moves.ids)] if len(moves) > 1 else False,
    }
```

Native `sale.order._create_invoices` produces one consolidated move when all source SOs share the same partner, currency, and fiscal position — which is the case for repair quotes of the same batch (same client, same template). If for any reason the native path returns multiple moves, we return a list view rather than failing.

### Section-header post-processing

```python
def _inject_repair_section_headers(self, move):
    """Insert a `line_section` header before each repair's invoice lines.
    Labels match today's wizard format."""
    self.ensure_one()
    # Group invoice lines by originating sale order
    lines_by_so = {}
    for line in move.invoice_line_ids.sorted('sequence'):
        sos = line.sale_line_ids.mapped('order_id')
        if not sos:
            continue
        so = sos[:1]
        lines_by_so.setdefault(so.id, []).append(line)

    seq = 0
    for so_id, lines in lines_by_so.items():
        so = self.env['sale.order'].browse(so_id)
        # Prefer the linked repair when unique (forward case); fall back to SO name (legacy case)
        if len(so.repair_order_ids) == 1:
            repair = so.repair_order_ids
            label = _("Réparation : %s") % repair.device_id_name
            if repair.serial_number:
                label += _(" (S/N: %s)") % repair.serial_number
        else:
            label = _("Devis : %s") % so.name

        seq += 1
        self.env['account.move.line'].create({
            'move_id': move.id,
            'display_type': 'line_section',
            'name': label,
            'sequence': seq,
        })
        for line in lines:
            seq += 1
            line.sequence = seq
```

Resequencing puts each section header immediately before its lines in the printed order. Native `_create_invoices` already coalesces lines per SO; we just prepend one header per SO contribution.

### Batch form button

```xml
<button name="action_invoice_approved_quotes"
        type="object"
        string="Facturer les devis acceptés"
        class="btn-primary"
        invisible="not has_invoiceable_quotes"/>
```

## Section 3 — Native `sale.order` button replacement

### View inheritance

New file `views/sale_order_views.xml`:

```xml
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
```

### `sale.order` method

```python
def action_invoice_repair_quote(self):
    self.ensure_one()
    repairs = self.repair_order_ids
    if not repairs:
        raise UserError(_("Ce devis n'est lié à aucune réparation."))
    batch = repairs[:1].batch_id
    if not batch:
        raise UserError(_("Ce devis n'est rattaché à aucun dossier de dépôt."))
    return batch._invoice_approved_quotes(repairs)
```

**Per-SO semantics (C.1):** invoices *only this SO*, not the whole batch. The user clicked on a specific document; the button respects that mental model. Batch consolidation is reached via the batch form's button.

## Section 4 — `account.move` auto-stamp defense

In `repair_extensions.py`, add a create override:

```python
class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._auto_stamp_repair_metadata()
        return moves

    def _auto_stamp_repair_metadata(self):
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

Runs on every `account.move` creation, no-op for non-repair invoices. Idempotent: if our button has already stamped, the fields are already set. Catches list-view bulk invoicing, scripted creation, future surfaces.

## Section 5 — Partial acceptance and pickup

### `action_mark_delivered` eligibility change

Current predicate (Theme B): `delivery_state == 'none' AND state in {'done', 'irreparable'}`.

New predicate: `delivery_state == 'none' AND (state in {'done', 'irreparable'} OR quote_state == 'refused')`.

Refused-quote repair processing inside the loop:

```python
for repair in eligible:
    if repair.quote_state == 'refused':
        # client takes the un-repaired device back
        if repair.state not in ('cancel', 'irreparable'):
            repair.state = 'cancel'
        repair.delivery_state = 'delivered'
        # no SAR, no invoice, no ready_for_pickup_notification concern
        continue
    # ... existing done/irreparable branch unchanged
```

### Invariants

- Consolidation helper iterates `quote_state == 'approved'` only — refused repairs never contribute invoice lines.
- Batch `delivery_state` compute (Theme B, already stored) naturally transitions to `delivered` when all eligible repairs (accepted + refused) are delivered. Abandoned repairs remain excluded from the eligibility set.
- `ready_for_pickup_notification` and SAR logic gate on `quote_state == 'approved'` (already true in sub-project 3); refused repairs short-circuit cleanly.

### What we do NOT do

- No new enum value like `returned_unrepaired`. `delivery_state='delivered'` covers the physical act; `quote_state='refused'` + `state='cancel'` + no invoice is the semantic marker.
- No new "Abandonner" button wiring beyond the existing `action_mark_abandoned`.
- No automatic re-quote trigger on refusal. The manager handles via the sub-project-2 "statuer" activity.

## Data model summary

### New fields

| Model | Field | Type | Stored | Notes |
|---|---|---|---|---|
| `repair.order` | `is_quote_invoiceable` | Boolean (computed) | ✗ | Gates the repair-form "Facturer le devis" button |
| `repair.batch` | `has_invoiceable_quotes` | Boolean (computed) | ✗ | Gates the batch-form button |

### Removed fields

| Model | Field | Notes |
|---|---|---|
| `repair.pricing.wizard` | `generation_type`, `batch_id`, `remaining_repair_ids`, `accumulated_lines_json`, `step_info` | Dead with batch-mode walkthrough |

### Modified methods

| Model | Method | Change |
|---|---|---|
| `repair.pricing.wizard` | `default_get` | CAS A (batch context) removed |
| `repair.pricing.wizard` | `action_confirm` | Routes only to quote creation |
| `repair.pricing.wizard` | `_create_global_sale_order` | Renamed to `_create_quote`; `batch_id` branches removed |
| `repair.order` | `action_open_pricing_wizard` | **Deleted** |
| `repair.batch` | `action_mark_delivered` | Refused-quote eligibility + `state='cancel'` side effect |
| `account.move` | `create` | Auto-stamp `repair_id` / `batch_id` from sale lines |

### New methods

| Model | Method | Purpose |
|---|---|---|
| `repair.order` | `action_invoice_repair_quote` | Delegates to batch helper with singleton |
| `repair.batch` | `action_invoice_approved_quotes` | Batch-form button handler |
| `repair.batch` | `_invoice_approved_quotes(repairs)` | Core helper, shared entry |
| `repair.batch` | `_inject_repair_section_headers(move)` | PDF segmentation post-processing |
| `account.move` | `_auto_stamp_repair_metadata` | Resolve via sale.line fallback |
| `sale.order` | `action_invoice_repair_quote` | Per-SO button handler (C.1 semantics) |

## Testing strategy

New file: `repair_custom/tests/test_quote_invoice_model.py`.

### `TestPricingWizardQuoteOnly`

| Test | Coverage |
|---|---|
| `test_wizard_creates_quote` | Run wizard, produces exactly one `sale.order` with `sale_order_template_id=sale_order_template_repair_quote`, linked via `repair.sale_order_id`, `computed_order_type='repair_quote'` |
| `test_wizard_has_no_generation_type_field` | `generation_type` not in `_fields` — regression guard |
| `test_wizard_rejects_existing_quote` | `UserError` if `repair.sale_order_id` already set |
| `test_wizard_available_on_walk_in` | Repair with `quote_state='none'` in `state='confirmed'` / `under_repair` / `done` — "Devis" button is visible, wizard runs and creates the SO normally |
| `test_wizard_no_batch_context` | Launching with `active_model='repair.batch'` context yields a wizard that ignores the batch (no `batch_id`, no `remaining_repair_ids`) |

### `TestApprovedQuoteInvoicing`

| Test | Coverage |
|---|---|
| `test_is_quote_invoiceable_compute` | `quote_state=approved` + `invoice_status='to invoice'` → `True`; any other combination → `False` |
| `test_repair_button_invoices_singleton` | `action_invoice_repair_quote` creates one draft `account.move` with `repair_id` and `batch_id` stamped, one section header |
| `test_invoice_line_ids_preserve_sale_link` | `move.invoice_line_ids.sale_line_ids.order_id.repair_order_ids` resolves back to the source repair |
| `test_button_hidden_when_invoiced` | After posting the move, `is_quote_invoiceable` → `False`, button hidden |

### `TestBatchConsolidation`

| Test | Coverage |
|---|---|
| `test_consolidation_three_accepted` | Batch with 3 accepted quotes → one `account.move`, 3 section headers in sequence order, `batch_id` stamped, `repair_id` empty (not unique) |
| `test_consolidation_mixes_pending_skipped` | Batch with 2 accepted + 1 pending → consolidated move contains only the 2 accepted |
| `test_consolidation_partial_acceptance` | Batch with 2 accepted + 1 refused → consolidated move contains only the 2 accepted, no refused section |
| `test_consolidation_empty_raises` | Batch with 0 accepted quotes → `UserError` |
| `test_section_header_label_format` | Headers read `Réparation : <device_name> (S/N: <sn>)` |
| `test_has_invoiceable_quotes_compute` | Flips correctly on quote_state / invoice_status changes |

### `TestNativeButtonPlumbing`

| Test | Coverage |
|---|---|
| `test_auto_stamp_on_account_move_create` | A `sale.order._create_invoices()` called outside our helpers still produces a move with `repair_id` / `batch_id` stamped |
| `test_auto_stamp_batch_unique_repair_ambiguous` | Move covering multiple repairs → `batch_id` stamped when unique; `repair_id` left empty |
| `test_auto_stamp_noop_on_non_out_invoice` | `move_type='out_refund'` or `entry` → no stamping |
| `test_auto_stamp_idempotent_when_prestamped` | Creating a move with `repair_id` already set → auto-stamp does nothing |
| `test_so_form_button_replacement` | On a repair-quote SO, `action_create_invoice` is invisible; `action_invoice_repair_quote` visible and functional |
| `test_so_form_button_invoices_only_self` (C.1) | SO belongs to a batch with 3 accepted quotes; clicking the per-SO button produces an invoice for only that one SO |

### `TestPartialAcceptanceDelivery`

| Test | Coverage |
|---|---|
| `test_batch_livrer_mixes_approved_and_refused` | Batch with 1 done+approved + 1 refused → both eligible; after `Livrer`, both `delivery_state='delivered'` |
| `test_refused_delivery_cancels_repair_state` | Refused repair's `state` → `cancel` after delivery (silent side effect) |
| `test_refused_delivery_no_sar_no_invoice` | Refused repair has no SAR stamped, no invoice generated |
| `test_batch_delivery_state_partial_to_delivered` | After delivering all eligible (mix of accepted + refused), batch `delivery_state='delivered'` |

### Sub-project-2 test sweep

Audit `tests/test_quote_lifecycle.py` for any assertion tied to grouped-SO semantics (decision #11). Expected impact: `test_write_hook_batch_sale_order_syncs_all_repairs` either deleted or rewritten to assert per-repair sync with individual SOs. Done during implementation, not designed here.

### Manual QA checklist

1. Create a batch with 3 repairs. Request quote on 1 (via the technician flow). Open the pricing wizard; verify it's quote-only (no radio), no "Appareil 1/3" step.
2. Complete the wizard. Verify one sale.order created, linked only to this repair, with template `repair_quote`.
3. Send the quote by mail, approve it via portal. Verify the native "Créer la facture" button on the SO is hidden; a "Facturer le devis" button appears.
4. Click the per-SO button. Verify one `account.move` with `repair_id` + `batch_id` stamped, one section header.
5. Request quote on the other 2 repairs; approve both; open the batch form. Click "Facturer les devis acceptés". Verify one consolidated invoice with 3 section headers (one per repair).
6. Repeat scenario 5 but refuse one of the 3 quotes. Verify consolidation excludes the refused one; invoice has 2 section headers.
7. On the batch, click "Livrer". Verify the refused repair transitions to `state='cancel'`, `delivery_state='delivered'`, no SAR, no ready-for-pickup notification. Accepted ones get SAR + delivered.
8. Pick any legacy batch whose SO covers multiple repairs (pre-Theme-A). Verify the batch button still works — native `_create_invoices` produces one move, section headers fall back to SO name for the multi-repair case.
9. Create an `account.move` directly from the accounting UI with sale lines from a repair quote. Verify `repair_id` and `batch_id` are auto-stamped on post.
10. Walk-in repair (no quote requested): create the repair, confirm it (skipping the technician's "Établir devis" button entirely — `quote_state` stays `'none'`). Verify the "Devis" button on the repair form is still visible. Click it, run the wizard, approve the resulting SO manually via `action_confirm`, click "Facturer le devis". Verify the three-click path produces the expected invoice.

## Migration plan

### Schema

None. No new stored fields beyond computed non-stored booleans.

### Data

None. Legacy `sale.order` records with `len(repair_order_ids) > 1` are left as-is. The consolidation helper's section-header logic gracefully handles the legacy shape (falls back to SO name when `len(so.repair_order_ids) != 1`).

### Manifest

Version bump `repair_custom/__manifest__.py`: `17.0.1.6.0` → `17.0.1.7.0`.

### Pricing-wizard call-site audit

Search for every XML / Python entry point to `repair.pricing.wizard`:

- `repair_views.xml:78` — "Devis/Facture" button → retire (covered by Section 1).
- `repair_views.xml:198` — "Devis/Facture" button → retire.
- `repair_views.xml:241` — "Créer le devis" from quote queue → keep, remove `default_generation_type='quote'` context.
- Any batch-view button → retire.
- `action_repair_pricing_wizard` top-level action — keep, but the form view loses the radio.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Native `_create_invoices` refuses to consolidate across SOs of the same batch (differing fiscal positions, currencies, pricelists) | Repair quotes all share the `sale_order_template_repair_quote` template and the same partner — these fields are consistent by construction. The helper's fallback to a multi-move return covers any unexpected case |
| The auto-stamp on `account.move.create` adds cost to every move creation | `@api.depends` analogy: the override short-circuits when `move_type != 'out_invoice'` or when both stamps already exist. For an artisan shop's invoice volume, negligible |
| Removing `generation_type` from the wizard breaks in-flight transactions mid-migration | Wizard is a `TransientModel` — no persisted state across requests. A user who has the wizard open during deploy simply re-opens it |
| Legacy SOs in `draft`/`sent` with multiple repairs still exist when Theme A deploys | The batch button's consolidation helper treats them as one SO contributing one section header (labeled by SO name). Manager can manually re-draft + re-create per-repair quotes if needed |
| Retiring the invoice-mode entry points leaves orphan button XML | Covered by the audit in the Migration section. Implementation task: grep for `default_generation_type='invoice'` and `%(action_repair_pricing_wizard)d` across views |
| Theme B's `_compute_delivery_state` didn't anticipate refused-quote repairs entering `delivery_state='delivered'` via `Livrer` | Existing eligibility set (`filtered(lambda r: r.delivery_state != 'abandoned')`) already includes refused-quote repairs. When all are delivered, the compute lands on `'delivered'` correctly — no change needed to that compute. Verified by `test_batch_delivery_state_partial_to_delivered` |
| Sub-project 2 CRON logic references grouped-SO behavior | Audit during implementation. Expected clean — the CRON filters `quote_state='sent'` per repair; the per-SO shape is already the natural fit |

## Git branch

`feature/repair-quote-invoice-model` from `main` after Theme B is merged (already done — commit `41481e6`).

## Boundary with prior sub-projects

**What Theme A invalidates:**

- Sub-project 2 decision #11 (one batch = one SO = one quote decision) — retired. The surrounding sync mechanism (`_apply_quote_state_transition`, `sale.order.write` override, CRON, shared queue) is unchanged because it already operates per-repair.
- Sub-project 2 decision #12 ("pricing_wizard stays as-is") — partially retired. Wizard stays as the quote entry point; invoice mode is removed.

**What Theme A preserves:**

- All of sub-project 1 (appointment system) — untouched.
- Sub-project 3 completion/pickup/SAR/warranty — untouched. The `action_post` hook's fallback path becomes the primary path for batch-consolidated invoices.
- Theme B's batch UX polish — untouched. The `delivery_state` compute, the `Livrer` button, the sibling banner, and the navigation bridge all continue to work.

**What a future Theme C might address (noted, not designed):**

- Mail template for "quote approved" notification to client (today: chatter only).
- Unified customer portal showing all of a deposit's quotes on a single page.
- Bulk quote mailing ("envoyer par mail" on the batch — send all quotes in one client email).
