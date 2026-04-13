# Warranty System Polish

**Date:** 2026-04-13
**Scope:** repair_custom module — warranty UX, data model, and settings cleanup
**Branch:** Single branch, all changes together

## Context

The warranty system is functionally correct: SAV (12mo, equipment sale) and SAR (3mo, repair) are tracked independently on `stock.lot`, with SAV taking priority in the computed `warranty_type`. This spec addresses UX friction, a missing convenience field, and config parameter housekeeping.

## Changes

### 1. Toast Notifications (replace blocking dialogs)

**Problem:** `_onchange_lot_workflow` in `repair_order.py` returns `{'warning': {...}}` which shows a blocking modal dialog. The desired behavior is a non-intrusive toast notification.

**Solution:** Replace the three `{'warning': ...}` return blocks (SAV active, SAR active, expired with history) with `ir.actions.client` / `display_notification` actions.

**Files:** `repair_custom/models/repair_order.py`

**Notification cases:**
- **SAV active:** info toast — expiry date, sale date
- **SAR active:** info toast — expiry date, previous technician, repair date
- **Expired with history:** info toast — previous technician, date, "Garantie expirée"
- **No history:** no notification (silent)

**Toast params:** `type: 'info'`, `sticky: False`

### 2. `last_technician_id` on `stock.lot`

**Problem:** The lot only stores `last_delivered_repair_id`. To find who last worked on a device (needed for SAR repair labels), you must navigate through the repair record.

**Solution:** Add a stored `Many2one('hr.employee')` field on `stock.lot`, stamped at delivery.

**Files:**
- `repair_custom/models/repair_extensions.py` — field definition
- `repair_custom/models/repair_order.py` — stamp in `action_repair_delivered`
- `repair_custom/wizard/device_stock_wizard.py` — clear on abandon
- `repair_custom/views/stock_lot_views.xml` — display in warranty tab
- `repair_custom/report/repair_label.xml` — available for SAR label

**Stamp logic:**
- On successful repair delivery (`state == 'done'`): write alongside `sar_expiry` and `last_delivered_repair_id`
- On irreparable delivery (`state != 'done'`): write alongside `last_delivered_repair_id` (no `sar_expiry`)
- On abandon: clear to `False` alongside other warranty fields

### 3. Config Settings Centralization

**Problem:** Three config parameters (`sar_warranty_months`, `sav_warranty_months`, `auto_validate_equipment_sale`) are only editable via System Parameters. The existing settings file uses manual `get_values`/`set_values` instead of the cleaner `config_parameter=` attribute.

**Solution:** Refactor `res_config_settings.py` to use `config_parameter=` for all fields (matching `repair_appointment` pattern), and add the three missing params.

**Files:**
- `repair_custom/models/res_config_settings.py` — refactor + add fields
- `repair_custom/views/res_config_settings_views.xml` — settings form view (add "Garantie" and "Ventes" sections)

**Full field list after refactor:**

| Field | config_parameter key | Type | Default |
|---|---|---|---|
| `repair_service_tax_id` | `repair_custom.service_tax_id` | Many2one | — |
| `quote_reminder_delay_days` | `repair_custom.quote_reminder_delay_days` | Integer | 5 |
| `quote_escalation_delay_days` | `repair_custom.quote_escalation_delay_days` | Integer | 3 |
| `sar_warranty_months` | `repair_custom.sar_warranty_months` | Integer | 3 |
| `sav_warranty_months` | `repair_custom.sav_warranty_months` | Integer | 12 |
| `auto_validate_equipment_sale` | `repair_custom.auto_validate_equipment_sale` | Boolean | True |

**Note on `Many2one` with `config_parameter=`:** Odoo stores the DB id as a string. Existing direct `get_param` calls in `repair_pricing_wizard.py:291` and `repair_extensions.py:583` already do `int(tax_id)`, so they remain compatible.

**Removal:** Delete `get_values()` and `set_values()` methods entirely.

### 4. Legacy Fallback Removal Documentation

**Problem:** `_compute_suggested_warranty` has a fallback branch (repair_order.py:263-270) that recalculates SAR eligibility from `previous_repair_id.end_date` instead of trusting the lot's `sar_expiry`. This exists for legacy data predating the warranty stamping system.

**Solution:** No code change. Add a comment block with removal instructions.

**Removal instructions (to include in code comment):**

1. Run verification query:
   ```sql
   SELECT sl.id, sl.name FROM stock_lot sl
   JOIN repair_order ro ON ro.lot_id = sl.id
   WHERE ro.state = 'done' AND sl.sar_expiry IS NULL;
   ```
2. If results exist: backfill `sar_expiry` from last delivered repair's `end_date + 3 months`
3. Once clean: delete the `elif rec.previous_repair_id` branch; the suggested warranty compute then only needs `lot_id.warranty_state`

## Design Decisions

- **SAR always written at delivery, even under active SAV:** SAV takes computed priority. The SAR provides fallback coverage after SAV expires. This benefits the customer and keeps the logic simple.
- **`last_technician_id` stamped for irreparable repairs too:** The technician did diagnostic work; this is useful context.
- **Toast notifications, not banners:** Banners persist on the form and add visual noise. Toasts appear once when the lot is selected, which matches the "heads-up" intent.

## Out of Scope

- Warranty duration changes (stays 3mo SAR, 12mo SAV)
- Public tracking page warranty display
- Warranty-related automated emails/activities
