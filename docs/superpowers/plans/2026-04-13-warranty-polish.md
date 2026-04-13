# Warranty System Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the warranty system with toast notifications, a `last_technician_id` field on `stock.lot`, centralized config settings, and legacy fallback removal documentation.

**Architecture:** Four independent changes touching `repair_order.py` (onchange + fallback comment), `repair_extensions.py` (new field on `stock.lot`), `res_config_settings.py` (modernize + add fields), their corresponding XML views, and `device_stock_wizard.py` (clear new field on abandon).

**Tech Stack:** Odoo 17 (Python 3.10+), XML views, `ir.config_parameter`

**Spec:** `docs/superpowers/specs/2026-04-13-warranty-polish-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `repair_custom/models/repair_order.py` | Modify | Toast notifications in `_onchange_lot_workflow`, fallback removal comment in `_compute_suggested_warranty` |
| `repair_custom/models/repair_extensions.py` | Modify | Add `last_technician_id` field on `stock.lot`, stamp in delivery |
| `repair_custom/models/res_config_settings.py` | Modify | Modernize to `config_parameter=` pattern, add 3 missing fields |
| `repair_custom/wizard/device_stock_wizard.py` | Modify | Clear `last_technician_id` on abandon |
| `repair_custom/views/res_config_settings_views.xml` | Modify | Add Garantie + Ventes blocks |
| `repair_custom/views/stock_lot_views.xml` | Modify | Show `last_technician_id` in warranty tab |

---

### Task 1: Toast Notifications

Replace blocking `{'warning': ...}` dialogs with `ir.actions.client` / `display_notification` toasts in `_onchange_lot_workflow`.

**Files:**
- Modify: `repair_custom/models/repair_order.py:280-326`

- [ ] **Step 1: Replace the three warning return blocks with display_notification**

In `repair_custom/models/repair_order.py`, replace lines 296-326 (everything after `if not lot_changed: return`) with:

```python
        lot = self.lot_id
        if lot.warranty_state == 'active' and lot.warranty_type == 'sav':
            sale_date_str = lot.sale_date.strftime('%d/%m/%Y') if lot.sale_date else '?'
            expiry_str = lot.warranty_expiry.strftime('%d/%m/%Y') if lot.warranty_expiry else '?'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Garantie SAV"),
                    'message': _("Garantie SAV jusqu'au %s (Vendu le %s)") % (expiry_str, sale_date_str),
                    'type': 'info',
                    'sticky': False,
                }
            }
        elif lot.warranty_state == 'active' and lot.warranty_type == 'sar':
            prev_repair = lot.last_delivered_repair_id or self.previous_repair_id
            tech_name = prev_repair.technician_employee_id.name if prev_repair and prev_repair.technician_employee_id else 'Inconnu'
            expiry_str = lot.warranty_expiry.strftime('%d/%m/%Y') if lot.warranty_expiry else '?'
            prev_date_str = (prev_repair.end_date or prev_repair.write_date).strftime('%d/%m/%Y')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Retour Garantie (SAR)"),
                    'message': _("Appareil sous garantie jusqu'au %s (Réparé par %s, le %s)") % (expiry_str, tech_name, prev_date_str),
                    'type': 'info',
                    'sticky': False,
                }
            }
        elif self.previous_repair_id:
            prev_repair = self.previous_repair_id
            tech_name = prev_repair.technician_employee_id.name or 'Inconnu'
            prev_date_str = (prev_repair.end_date or prev_repair.write_date).strftime('%d/%m/%Y')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Hors Garantie"),
                    'message': _("Cet appareil a déjà été réparé par %s le %s (Garantie expirée)") % (tech_name, prev_date_str),
                    'type': 'info',
                    'sticky': False,
                }
            }
```

- [ ] **Step 2: Manual test**

Start Odoo in dev mode:
```bash
cd /Users/martin/Documents/odoo_dev/odoo && workon odoo_dev && ./odoo-bin -c ../odoo.conf --dev=reload,xml -u repair_custom
```

1. Open a repair order in draft state
2. Select a lot that has an active SAV warranty → verify a non-blocking toast appears (bottom-right, auto-dismisses)
3. Select a lot that has an active SAR warranty → verify toast with technician name
4. Select a lot with expired warranty history → verify "Hors Garantie" toast
5. Select a lot with no history → verify no notification

- [ ] **Step 3: Commit**

```bash
git add repair_custom/models/repair_order.py
git commit -m "repair_custom: switch warranty notifications from modal to toast"
```

---

### Task 2: Add `last_technician_id` on `stock.lot`

Add a stored field tracking who last repaired a device, stamped at delivery.

**Files:**
- Modify: `repair_custom/models/repair_extensions.py:67-69` (add field)
- Modify: `repair_custom/models/repair_order.py:746-759` (stamp at delivery)
- Modify: `repair_custom/wizard/device_stock_wizard.py:203-210` (clear on abandon)
- Modify: `repair_custom/views/stock_lot_views.xml:113-119` (display in warranty tab)

- [ ] **Step 1: Add field definition on `stock.lot`**

In `repair_custom/models/repair_extensions.py`, after line 69 (`sar_expiry` field), add:

```python
    last_technician_id = fields.Many2one(
        'hr.employee', string="Dernier Technicien",
        readonly=True, copy=False,
    )
```

- [ ] **Step 2: Stamp at delivery in `action_repair_delivered`**

In `repair_custom/models/repair_order.py`, replace the two `lot_id.write()` calls in the delivery block (lines 753 and 756-758).

Replace the irreparable case (line 753):
```python
                rec.lot_id.write({
                    'last_delivered_repair_id': rec.id,
                    'last_technician_id': rec.technician_employee_id.id,
                })
```

Replace the successful repair case (lines 756-759):
```python
            sar_expiry = fields.Date.today() + relativedelta(months=sar_months)
            rec.lot_id.write({
                'last_delivered_repair_id': rec.id,
                'last_technician_id': rec.technician_employee_id.id,
                'sar_expiry': sar_expiry,
            })
```

- [ ] **Step 3: Clear on abandon**

In `repair_custom/wizard/device_stock_wizard.py`, add `'last_technician_id': False,` to the warranty field reset dict at line 204:

```python
        self.lot_id.write({
            'sale_date': False,
            'sav_expiry': False,
            'sale_order_id': False,
            'last_delivered_repair_id': False,
            'last_technician_id': False,
            'sar_expiry': False,
        })
```

- [ ] **Step 4: Display in warranty tab on lot form**

In `repair_custom/views/stock_lot_views.xml`, inside the warranty tab's second `<group>` (after the `sar_expiry` field at line 118), add:

```xml
                            <field name="last_technician_id" invisible="not last_technician_id"/>
```

- [ ] **Step 5: Update module and manual test**

```bash
cd /Users/martin/Documents/odoo_dev/odoo && workon odoo_dev && ./odoo-bin -c ../odoo.conf -u repair_custom --dev=reload,xml
```

1. Open a repair order with a lot, complete the repair, deliver it → check the lot's warranty tab shows the technician
2. Create a new repair for the same lot → the repair label should show the technician name in the SAR header line
3. Trigger an abandon via the device stock wizard → verify the lot's `last_technician_id` is cleared

- [ ] **Step 6: Commit**

```bash
git add repair_custom/models/repair_extensions.py repair_custom/models/repair_order.py repair_custom/wizard/device_stock_wizard.py repair_custom/views/stock_lot_views.xml
git commit -m "repair_custom: add last_technician_id on stock.lot, stamp at delivery"
```

---

### Task 3: Centralize Config Settings

Modernize `res_config_settings.py` to use `config_parameter=` attribute and add the 3 missing parameters.

**Files:**
- Modify: `repair_custom/models/res_config_settings.py` (full rewrite)
- Modify: `repair_custom/views/res_config_settings_views.xml` (add Garantie + Ventes blocks)

- [ ] **Step 1: Rewrite `res_config_settings.py`**

Replace the entire content of `repair_custom/models/res_config_settings.py` with:

```python
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    repair_service_tax_id = fields.Many2one(
        'account.tax',
        string="TVA Services (Réparations/Locations)",
        config_parameter='repair_custom.service_tax_id',
        help="Taxe appliquée automatiquement sur toutes les lignes de réparations et de locations.",
        domain=[('type_tax_use', '=', 'sale'), ('amount_type', '=', 'percent')],
    )

    quote_reminder_delay_days = fields.Integer(
        string="Délai avant relance devis (jours)",
        config_parameter='repair_custom.quote_reminder_delay_days',
        default=5,
        help="Nombre de jours après l'envoi du devis avant qu'un rappel automatique soit envoyé au client.",
    )

    quote_escalation_delay_days = fields.Integer(
        string="Délai avant escalade devis (jours)",
        config_parameter='repair_custom.quote_escalation_delay_days',
        default=3,
        help="Nombre de jours après la relance (ou après un clic 'Contacté') avant qu'une activité d'escalade soit créée pour le manager.",
    )

    sar_warranty_months = fields.Integer(
        string="Durée garantie SAR (mois)",
        config_parameter='repair_custom.sar_warranty_months',
        default=3,
        help="Durée de la garantie SAR (Service Après Réparation) en mois.",
    )

    sav_warranty_months = fields.Integer(
        string="Durée garantie SAV (mois)",
        config_parameter='repair_custom.sav_warranty_months',
        default=12,
        help="Durée de la garantie SAV (Service Après Vente) en mois.",
    )

    auto_validate_equipment_sale = fields.Boolean(
        string="Valider automatiquement les livraisons équipement",
        config_parameter='repair_custom.auto_validate_equipment_sale',
        default=True,
        help="Valider automatiquement les bons de livraison lors de la confirmation d'une vente d'équipement.",
    )
```

- [ ] **Step 2: Add Garantie and Ventes blocks to the settings view**

In `repair_custom/views/res_config_settings_views.xml`, add two new `<block>` elements after the existing "Cycle de vie du devis" block (after line 25, before `</app>`):

```xml
                    <block title="Garantie">
                        <setting string="Durée garantie SAR"
                                 help="Durée de la garantie SAR (Service Après Réparation) accordée après chaque réparation livrée.">
                            <div class="content-group">
                                <div class="row mt8">
                                    <label for="sar_warranty_months" class="col-lg-4"/>
                                    <field name="sar_warranty_months" class="col-lg-2"/>
                                </div>
                            </div>
                        </setting>
                        <setting string="Durée garantie SAV"
                                 help="Durée de la garantie SAV (Service Après Vente) accordée lors d'une vente d'équipement.">
                            <div class="content-group">
                                <div class="row mt8">
                                    <label for="sav_warranty_months" class="col-lg-4"/>
                                    <field name="sav_warranty_months" class="col-lg-2"/>
                                </div>
                            </div>
                        </setting>
                    </block>
                    <block title="Ventes Équipement">
                        <setting string="Validation automatique des livraisons"
                                 help="Valider automatiquement les bons de livraison lors de la confirmation d'une vente d'équipement.">
                            <field name="auto_validate_equipment_sale"/>
                        </setting>
                    </block>
```

- [ ] **Step 3: Update module and manual test**

```bash
cd /Users/martin/Documents/odoo_dev/odoo && workon odoo_dev && ./odoo-bin -c ../odoo.conf -u repair_custom --dev=reload,xml
```

1. Go to Settings → Réparations
2. Verify all 6 fields are visible and grouped correctly (Comptabilité, Cycle de vie du devis, Garantie, Ventes Équipement)
3. Change SAR warranty months to 6, save → go to System Parameters and verify `repair_custom.sar_warranty_months` is now `6`
4. Reset to 3, save
5. Toggle `auto_validate_equipment_sale` off and on, verify it persists

- [ ] **Step 4: Commit**

```bash
git add repair_custom/models/res_config_settings.py repair_custom/views/res_config_settings_views.xml
git commit -m "repair_custom: centralize config settings with config_parameter= pattern"
```

---

### Task 4: Legacy Fallback Removal Documentation

Add a comment block to `_compute_suggested_warranty` with removal instructions.

**Files:**
- Modify: `repair_custom/models/repair_order.py:263-270`

- [ ] **Step 1: Add the removal comment**

In `repair_custom/models/repair_order.py`, add a comment block before line 263 (`elif rec.previous_repair_id:`):

```python
            # LEGACY FALLBACK — safe to remove once all historical repairs
            # have been migrated (lot.sar_expiry populated for all past repairs).
            #
            # To remove:
            # 1. Run check query to verify no lots with repair history lack sar_expiry:
            #    SELECT sl.id, sl.name FROM stock_lot sl
            #    JOIN repair_order ro ON ro.lot_id = sl.id
            #    WHERE ro.state = 'done' AND sl.sar_expiry IS NULL;
            # 2. If results: backfill sar_expiry from last delivered repair's
            #    end_date + 3 months
            # 3. Once clean, delete this elif branch — suggested_warranty
            #    then only needs to check lot_id.warranty_state
            elif rec.previous_repair_id:
```

- [ ] **Step 2: Commit**

```bash
git add repair_custom/models/repair_order.py
git commit -m "repair_custom: document legacy warranty fallback removal instructions"
```
