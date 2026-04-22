# Repair Workflow Polish — Batch/Repair UX & Lifecycle — Design Spec

**Date:** 2026-04-22
**Target module:** `repair_custom` (extension, no new addon)
**Depends on:** `repair_custom`, `repair_appointment`
**Branch:** `feature/repair-batch-ux-polish` (from `main`)

## Context and scope

The three-sub-project repair workflow overhaul (appointment, quote lifecycle, completion → pickup → SAR) shipped the end-to-end happy path. Day-to-day usage surfaced a set of polish issues around the `repair.order` ↔ `repair.batch` relationship:

- The manager lives in `repair.order` views; the bridge to `repair.batch` is not intuitive.
- The atelier (workshop) works per-repair, but technicians lack context on sibling repairs from the same deposit.
- Sub-project 3 auto-created the singleton batch at `repair.order.create()` time. Readonly rules (designed to enforce cross-repair consistency once a deposit exists) kick in before the user has finished entering a repair, blocking edits while still in draft.
- No manual `Livrer` button on the batch form; batch-level delivery state is not visible at a glance.
- Deleting the last repair of a batch leaves an orphan batch in the system.

This spec groups the fixes as **Theme B**: UX and lifecycle polish that do not touch the quote or invoice model. A companion **Theme A** (quote & invoice model rework) is deferred to its own brainstorm and spec, because it inverts a load-bearing decision from sub-project 2 ("one batch = one sale.order = one quote decision") and deserves separate treatment.

### Out of scope for this spec (deferred to Theme A)

- Any change to the quote generation or storage model (pricing wizard stays as-is).
- Partial-acceptance scenario: client deposits N devices, only some quotes are accepted — invoicing and pickup flow rework.
- Preserving the repair ↔ invoice link when invoicing via the native `sale.order` "Créer la facture" button (currently, going through native flow loses the `repair_id` on `account.move`, which breaks the sub-project 3 `action_post` hook).
- Easier grouping of accepted quotes of a batch into one invoice.

### Out of scope globally

- No schema migration. The `batch_id required=True` relaxation is a model-level change only; existing data already has batches populated by sub-project 3's pre-migration.
- No new tests around the quote lifecycle — Theme A will revisit.
- No changes to `repair.pickup.appointment` or the portal.

## Decisions captured from brainstorming

| # | Decision | Why |
|---|---|---|
| 1 | Split the polish work into two themes; Theme B first (UX/lifecycle), Theme A (quote/invoice model) later | Theme B is largely view/button/constraint work with small blast radius; Theme A is architectural and benefits from a focused design session after Theme B lands |
| 2 | Batch creation moves from `repair.order.create()` to `action_confirm` | Every downstream code path that needs a batch (quote, appointment, pickup, invoice, SAR) runs at `state >= confirmed`. Draft repairs never touch those paths. Creating the batch only at confirm preserves the invariant where it matters and keeps draft-state editing frictionless |
| 3 | Readonly rules are kept as-is (they already guard cross-repair consistency once a batch exists) | The readonly rules are correct post-confirm. With decision #2, they stop triggering prematurely because there is no batch in draft |
| 4 | `batch_id` schema constraint weakened: `required=False` at ORM level; enforcement in `action_confirm` instead | SQL `NOT NULL` would block the new flow. A Python-level guard at confirm is the correct boundary |
| 5 | Aggregated `delivery_state` on `repair.batch` (computed, stored): `none` / `partial` / `delivered` / `abandoned` | Gives manager-at-a-glance status; powers row decoration and form badge. Stored for fast filtering |
| 6 | `decoration-success="delivery_state == 'delivered'"` on BOTH the batch tree view AND the repair tree view | User request for consistent visual language across the two surfaces |
| 7 | Batch-form `Livrer` button: one-click bulk action, same eligibility predicate as per-repair `Livrer`. Delegates to existing `batch.action_mark_delivered` | Doctrine stays "one deposit = one batch = one pickup". Partial-pickup edge cases still use the per-repair button |
| 8 | Sibling-repair awareness on the repair form = chip banner above the notebook. Visible only when `batch.repair_count > 1` | Passive visibility beats a tab click for techs. Stays out of the way for singleton repairs (common case) |
| 9 | Delete cascade: `repair.order.unlink()` archives (not deletes) the batch when the last active repair is gone. Symmetric on archive/un-archive | Batches may have FK references (sale orders, invoices, appointments, chatter). Archive preserves history; delete would orphan or raise |
| 10 | Manager navigation bridge: three additive surfaces — beefed-up smart button (sibling count), `batch_id` column on repair tree, "Grouper par dossier" toggle in search view | Each addresses a different task (drill-down, list-glance, think-in-batches). All cheap view-only changes |
| 11 | `repair.batch` tree view is kept but demoted: default filter narrowed to `repair_count > 1`; smart button from repair form lands on the batch **form** (not tree) | Batch-native surfaces (aggregated delivery state, appointments, archive management) still need their own view. Singleton batches stay hidden by default so they don't clutter the list |

## Architecture

All changes live inside `repair_custom`. One Python module is touched across the five items; the rest is XML (views and a chip template). No new files except tests.

### Module layout (delta)

```
repair_custom/
├── __manifest__.py                    # MODIFIED: version bump 17.0.1.6.0
├── models/
│   ├── repair_order.py                # MODIFIED:
│   │                                  #   - create() no longer auto-wraps batch
│   │                                  #   - batch_id required=False
│   │                                  #   - action_confirm auto-wraps batch
│   │                                  #   - unlink() archive-cascades batch
│   │                                  #   - write() archive-cascade symmetry on active=False
│   │                                  #   - sibling_repair_ids computed field
│   └── repair_batch.py                # MODIFIED:
│                                      #   - delivery_state computed stored field
│                                      #   - action_mark_delivered entry-point wired to form button
├── views/
│   ├── repair_views.xml               # MODIFIED:
│   │                                  #   - sibling-repair banner QWeb block
│   │                                  #   - batch_id column on repair tree
│   │                                  #   - "Grouper par dossier" filter in search view
│   │                                  #   - decoration-success on delivery_state
│   │                                  #   - smart-button label shows sibling count
│   └── repair_batch_views.xml         # MODIFIED:
│                                      #   - delivery_state badge in form header
│                                      #   - delivery_state column + decoration-success on tree
│                                      #   - "Livrer" header button
│                                      #   - default search filter repair_count > 1
└── tests/
    └── test_batch_ux_polish.py        # NEW
```

No schema migration required. Module version bump is for the manifest-level change (new field on batch, new view XML IDs).

## Section 1 — Deferred batch creation

### `repair.order.create()` change

Remove the create-time batch wrap introduced by sub-project 3. The method reverts to its pre-sub-project-3 form for batch assignment: `batch_id` is set explicitly (e.g., by `action_add_device_to_batch`) or left empty.

```python
@api.model_create_multi
def create(self, vals_list):
    for vals in vals_list:
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('repair.order') or 'New'
    return super().create(vals_list)
```

### `batch_id` field relaxation

```python
batch_id = fields.Many2one(
    'repair.batch', string="Dossier de Dépôt",
    readonly=True, index=True, ondelete='restrict',
    # required=True  -- removed; enforced in action_confirm
)
```

### `action_confirm` auto-wrap

```python
def action_confirm(self):
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
    return super().action_confirm()
```

Batch is created before `super()` so the confirm transition sees a populated `batch_id` and downstream computes (state, `ready_for_pickup_notification`) fire correctly on the first write.

### What this changes for the user

- Editing a draft repair's fields (partner, device, pickup location, etc.) is unblocked because no batch exists yet — the readonly rules that enforce sibling consistency simply don't engage.
- On confirmation, the batch is materialized and all existing cross-repair discipline resumes untouched.
- `action_add_device_to_batch` still creates the batch early when the user explicitly wants to group a new device with an existing batch — unchanged.

### Edge cases

- A draft repair that is never confirmed has no batch. It doesn't appear in batch-centric searches, which is correct — it's not a "deposit event" yet.
- Existing historical data is untouched (sub-project 3's pre-migration already populated every repair).
- The `batch.repair_count` compute remains; draft-only repairs simply don't participate.

## Section 2 — Batch delivery state + `Livrer` button

### New field on `repair.batch`

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

### Form header — badge

Standard Odoo `widget="statusbar"` on the existing state line, or a separate `<field name="delivery_state" widget="badge"/>` next to `state`. A single-line visual sync with the repair form's delivery state.

### Tree view — column + decoration

Batch tree view (`repair_batch_views.xml`):

```xml
<field name="delivery_state"/>
<tree decoration-success="delivery_state == 'delivered'" ...>
```

Repair tree view (`repair_views.xml`), add to existing decorations:

```xml
<tree decoration-success="delivery_state == 'delivered'" ...>
```

Existing decorations on the repair tree are preserved; `decoration-success` is additive.

### Header button `Livrer` on batch form

```xml
<button name="action_mark_delivered"
        type="object"
        string="Livrer"
        class="btn-primary"
        invisible="delivery_state in ('delivered', 'abandoned')"/>
```

Wired to the existing `batch.action_mark_delivered` from sub-project 3 (same predicate: eligible repairs have `delivery_state='none'` and `state in {'done','irreparable'}`). Raises `UserError` if no eligible repair is found, per the existing method's guard.

No change to the method itself — it already implements the correct loop (stamp SAR on done, skip SAR on irreparable, create customer picking, mark appointment done).

## Section 3 — Sibling-repair banner

### New computed field on `repair.order`

```python
sibling_repair_ids = fields.One2many(
    'repair.order',
    compute='_compute_sibling_repair_ids',
    string="Autres réparations du dossier",
)

@api.depends('batch_id', 'batch_id.repair_ids')
def _compute_sibling_repair_ids(self):
    for rec in self:
        if not rec.batch_id or rec.batch_id.repair_count <= 1:
            rec.sibling_repair_ids = False
            continue
        rec.sibling_repair_ids = rec.batch_id.repair_ids - rec

has_siblings = fields.Boolean(
    compute='_compute_has_siblings',
    store=False,
)

@api.depends('sibling_repair_ids')
def _compute_has_siblings(self):
    for rec in self:
        rec.has_siblings = bool(rec.sibling_repair_ids)
```

Non-stored to avoid recomputation storms on batch writes; cheap since `batch_id.repair_ids` is already indexed.

### View change

Banner above the notebook, inside the form sheet:

```xml
<div class="o_form_batch_siblings" invisible="not has_siblings">
    <span class="o_form_batch_siblings_label">Autres appareils du dossier :</span>
    <field name="sibling_repair_ids" widget="many2many_tags_avatar" readonly="1" options="{'no_create': True}"/>
</div>
```

Or, for richer chips, a QWeb `t-foreach` block rendering:

```
[REP-XXXX · <device name> · <state icon> · <delivery icon>]
```

Each chip is a link (`action="repair_custom.action_repair_order"` with `res_id`) opening the sibling. If the `many2many_tags_avatar` widget renders acceptably with state color badges, use it; otherwise fall back to the QWeb block. Decision made during implementation, visual only.

No changes to data or methods.

## Section 4 — Archive cascade on delete/archive

### `repair.order.unlink()` override

```python
def unlink(self):
    batches = self.mapped('batch_id')
    res = super().unlink()
    for batch in batches.exists():
        if not batch.repair_ids.filtered('active'):
            batch.active = False
    return res
```

### `repair.order.write()` symmetry

```python
def write(self, vals):
    res = super().write(vals)
    if 'active' in vals:
        batches = self.mapped('batch_id').exists()
        for batch in batches:
            active_children = batch.repair_ids.filtered('active')
            if vals['active'] is False and not active_children and batch.active:
                batch.active = False
            elif vals['active'] is True and active_children and not batch.active:
                batch.active = True
    return res
```

### Invariants preserved

- Any active repair implies an active batch.
- An archived batch with linked sale orders, invoices, appointments, or chatter remains queryable (no FK deletion).
- `ondelete='restrict'` on `batch_id` still protects against direct `batch.unlink()` when repairs reference it — archival is the only deactivation path.

## Section 5 — Manager navigation bridge

### Smart button on repair form

Existing smart button reworked to show sibling count:

```xml
<button name="action_open_batch"
        type="object"
        class="oe_stat_button"
        icon="fa-folder-open">
    <field name="batch_sibling_count" widget="statinfo" string="Dossier"/>
</button>
```

With:

```python
batch_sibling_count = fields.Integer(
    compute='_compute_batch_sibling_count',
    string="Réparations dans le dossier",
)

@api.depends('batch_id.repair_count')
def _compute_batch_sibling_count(self):
    for rec in self:
        rec.batch_sibling_count = rec.batch_id.repair_count if rec.batch_id else 0
```

Click opens the batch form (not tree) via a plain `action_open_batch` helper returning an `act_window` with `res_id=self.batch_id.id`.

Hidden when `batch_id` is empty (draft repairs — see Section 1).

### `batch_id` column on repair tree view

Add `<field name="batch_id"/>` to the existing tree view. Click-through is native (Odoo's M2O cell links to the record).

### "Grouper par dossier" filter

In the repair search view:

```xml
<filter name="group_by_batch"
        string="Dossier"
        context="{'group_by': 'batch_id'}"/>
```

Manager can toggle it to collapse repairs under their batch reference. Works with other group-bys (state, technician) via the standard Odoo multi-group-by.

### Batch tree view default filter

```xml
<filter name="multi_device_batches"
        string="Dossiers multi-appareils"
        domain="[('repair_count', '>', 1)]"/>
```

Pre-activated via the action `context="{'search_default_multi_device_batches': 1}"` so the default landing is only non-singleton batches. Singleton batches (created at confirm for standalone repairs) are still accessible by toggling the filter off.

## Data model summary

### New fields

| Model | Field | Type | Stored | Notes |
|---|---|---|---|---|
| `repair.batch` | `delivery_state` | Selection | ✓ | Aggregated from `repair_ids.delivery_state` + `repair_ids.state` |
| `repair.order` | `sibling_repair_ids` | O2M (computed) | ✗ | Peer repairs in the same batch, excluding self |
| `repair.order` | `has_siblings` | Boolean (computed) | ✗ | For `invisible` attrs on the banner |
| `repair.order` | `batch_sibling_count` | Integer (computed) | ✗ | Powers the smart-button label |

### Modified fields

| Model | Field | Change |
|---|---|---|
| `repair.order` | `batch_id` | `required=True` removed |

### Modified methods

| Model | Method | Change |
|---|---|---|
| `repair.order` | `create()` | Remove sub-project-3 batch auto-wrap |
| `repair.order` | `action_confirm()` | Add batch auto-wrap guard |
| `repair.order` | `unlink()` | Archive-cascade empty batches |
| `repair.order` | `write()` | Archive/un-archive symmetry |
| `repair.batch` | `_compute_delivery_state` | New |

### New public methods

| Model | Method | Purpose |
|---|---|---|
| `repair.order` | `action_open_batch` | Smart-button handler opening the batch form |

No new public methods on `repair.batch` — the existing `action_mark_delivered` is reused.

## Testing strategy

New test file: `repair_custom/tests/test_batch_ux_polish.py`.

### `TestDeferredBatchCreation`

| Test | Coverage |
|---|---|
| `test_create_repair_without_batch` | Creating a repair with `partner_id` but no `batch_id` leaves `batch_id` empty in draft |
| `test_confirm_creates_batch_when_missing` | `action_confirm` on a batchless repair populates `batch_id` with a fresh singleton |
| `test_confirm_keeps_existing_batch` | `action_confirm` on a repair that already has a batch reuses it |
| `test_confirm_requires_partner` | `action_confirm` raises `UserError` if `partner_id` is missing |
| `test_draft_repair_fields_editable` | Fields that become readonly post-confirm are writable while `state == 'draft'` and `batch_id` is empty |
| `test_action_add_device_to_batch_unchanged` | Existing flow still adds a new repair to the existing batch |

### `TestBatchDeliveryState`

| Test | Coverage |
|---|---|
| `test_delivery_state_none_default` | Batch with repairs all in `delivery_state='none'` → `delivery_state='none'` |
| `test_delivery_state_partial` | Some delivered, some none → `partial` |
| `test_delivery_state_delivered_all` | All eligible delivered → `delivered` |
| `test_delivery_state_abandoned_all` | All repairs abandoned → `abandoned` |
| `test_delivery_state_ignores_abandoned` | One abandoned + one delivered → `delivered` (abandoned excluded from the eligibility set) |
| `test_batch_livrer_button` | Calling `batch.action_mark_delivered()` from the batch form flips all eligible repairs to delivered and recomputes the aggregate |
| `test_batch_livrer_button_no_eligible_raises` | `UserError` when no eligible repair exists |

### `TestSiblingBanner`

| Test | Coverage |
|---|---|
| `test_has_siblings_singleton` | `has_siblings == False` for a batch with one repair |
| `test_has_siblings_multi` | `has_siblings == True` for a batch with ≥2 repairs |
| `test_sibling_list_excludes_self` | `sibling_repair_ids` does not include the current repair |
| `test_sibling_list_updates_on_batch_change` | Moving a repair to a different batch recomputes sibling set for both sides |

### `TestArchiveCascade`

| Test | Coverage |
|---|---|
| `test_unlink_last_repair_archives_batch` | Deleting the sole repair of a batch archives the batch |
| `test_unlink_with_siblings_keeps_batch_active` | Deleting one of two repairs leaves the batch active |
| `test_archive_last_repair_archives_batch` | `active=False` on the sole active repair archives the batch |
| `test_unarchive_repair_unarchives_batch` | `active=True` on a repair whose batch is archived un-archives the batch |
| `test_archive_does_not_archive_batch_with_active_siblings` | Archiving one repair of two leaves the batch active |

### `TestNavigationBridge`

| Test | Coverage |
|---|---|
| `test_batch_sibling_count_compute` | Count matches `batch_id.repair_count` |
| `test_action_open_batch_returns_form` | Action dict targets the batch form with the right `res_id` |
| `test_group_by_batch_filter` | Repairs appear grouped under batch when the filter is active (integration-level, may be skipped if Odoo test harness doesn't render views) |

### Fixture

Extend `repair_custom/tests/common.py` with:
- A multi-device batch (2 repairs: one `done`, one `irreparable`).
- A singleton batch (1 repair in `confirmed`).
- A standalone draft repair (no batch, for Section 1 tests).

### Manual QA checklist

1. Open a draft repair; change partner, device, pickup location — no readonly blocking.
2. Confirm the repair; verify batch is auto-created and becomes visible on the form.
3. Open an existing multi-device batch; verify `delivery_state` badge and the `Livrer` button.
4. Click `Livrer`; verify all eligible repairs flip to delivered, SAR stamped on done lots, appointment marked done.
5. Open any repair in a multi-device batch; verify the sibling banner appears with click-through chips.
6. Delete the last repair of a batch; verify the batch is archived (not deleted).
7. Archive a repair; verify the batch archive-cascade fires when appropriate.
8. Un-archive a repair whose batch is archived; verify the batch re-activates.
9. On the repair tree view, toggle "Grouper par dossier"; verify grouping.
10. On the repair tree view, verify the `batch_id` column renders and click-through works.
11. On the repair form, verify the smart button shows "Dossier (N)" with the right count and opens the batch form.
12. On the batch tree view, verify `decoration-success` on rows with `delivery_state='delivered'` and that the default filter hides singleton batches.

## Migration plan

### Schema

The `batch_id required=True` relaxation removes the NOT NULL constraint on `repair_order.batch_id`. Odoo's ORM handles the ALTER on upgrade.

### Data

None. Sub-project 3's pre-migration already populated `batch_id` on every historical repair. Going forward, new draft repairs can have `batch_id=NULL`, which is the desired new behavior.

### Manifest

Version bump `repair_custom/__manifest__.py`: `17.0.1.5.0` → `17.0.1.6.0`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Readonly rules on `repair.order` were implicitly relying on `batch_id` being populated at create-time | Audit the view XML for attrs and the Python constraints during implementation; where they reference `batch_id`, verify behavior is correct when null (i.e., draft) |
| Archive cascade might surprise users who archive a repair and don't expect the batch to disappear from their default view | Batch archival is reversible; un-archive symmetry keeps the invariant clean. Manual QA item #8 covers this |
| Sibling banner rendering using `many2many_tags_avatar` may not show state/delivery icons as intended | Fall back to a QWeb chip block if the native widget is insufficient. Visual-only decision |
| The `delivery_state` compute adds a write-time cost on every `repair.order` state or delivery_state change | For an artisan shop with dozens of active repairs, negligible. Stored is correct for filter/search performance |

## Git branch

`feature/repair-batch-ux-polish` from `main`. All Theme B work happens on this branch. Theme A will branch from `main` after Theme B merges.

## Theme A — deferred

The following bullets from the original brainstorm are deferred to a separate spec. Left here so the next session can pick up the user intent verbatim.

- **Quote model**: quotes are always per-repair, never grouped. The pricing wizard is the portal to create a first document, but when a repair already has a quote, invoicing must start from the existing quote(s).
- **Invoice grouping**: must be able to group accepted quotes of a batch into one invoice easily.
- **Native sale.order flow preservation**: when a repair/batch already has a confirmed sale order and the user invoices from the native `sale.order` "Créer la facture" button, the link to the repair/batch is lost → sub-project 3's `account.move.action_post` hook does not fire → completion logic (delivered, warranty, appointment-done) breaks. Must be preserved.
- **Partial acceptance scenario**: client deposits N devices, one quote per device, only some accepted. How does invoicing proceed? How does the pickup appointment cover delivered + refused devices? This inverts sub-project 2 decision #11 ("one batch = one sale.order = one quote decision") and needs explicit design.

When starting Theme A, reference this spec plus the sub-project 2 design (`docs/superpowers/specs/2026-04-11-repair-quote-lifecycle-design.md`), which decision #11 will be revisited.
