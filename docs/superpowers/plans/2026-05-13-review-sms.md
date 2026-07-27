# Google Review SMS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send an automatic Google review SMS X days after a repair's `delivery_state` becomes `delivered`, with a configurable dedup window and a manual cancel button.

**Architecture:** New mixin file extending `repair.order` adds 4 fields, a write-override that schedules the SMS on delivery, and a cron that sends pending SMS via Odoo IAP. Configuration lives in existing `res.config.settings`. SMS body and Google review link live in an `sms.template` editable from the Technical menu.

**Tech Stack:** Odoo 17, Python 3.10+, Odoo `sms` module + IAP gateway, `mail.thread` chatter, XML data files.

**Spec reference:** `docs/superpowers/specs/2026-05-13-review-sms-design.md`

**Key existing references:**
- `repair_custom/models/repair_order.py:186` — `delivery_state` field
- `repair_custom/models/repair_order.py:886` — write of `delivery_state='delivered'` and `end_date`
- `repair_custom/models/res_config_settings.py` — pattern for config_parameter fields
- `repair_custom/data/cron_data.xml` — pattern for cron records
- `repair_custom/tests/common.py` — `RepairQuoteCase` test fixture
- `repair_custom/__manifest__.py` — declares `data` files; needs `sms` dep

**Note on delivery timestamp:** The codebase has no `delivery_date` field. `end_date` is set at the same time as `delivery_state='delivered'` (line 888). We use `end_date` as the delivery timestamp.

---

## File Structure

**Create:**
- `repair_custom/models/repair_review_sms.py` — model extension (~120 lines)
- `repair_custom/data/review_sms_template_data.xml` — `sms.template` fixture
- `repair_custom/data/review_sms_cron_data.xml` — `ir.cron` record
- `repair_custom/tests/test_review_sms.py` — unit tests

**Modify:**
- `repair_custom/__manifest__.py` — add `sms` dependency, register new data files
- `repair_custom/models/__init__.py` — import new module
- `repair_custom/models/res_config_settings.py` — add 3 config fields
- `repair_custom/views/res_config_settings_views.xml` — add settings block
- `repair_custom/views/repair_views.xml` — add cancel button + status group on form

---

## Task 1: Manifest, dependency, and module skeleton

**Files:**
- Modify: `repair_custom/__manifest__.py`
- Modify: `repair_custom/models/__init__.py`
- Create: `repair_custom/models/repair_review_sms.py`

- [ ] **Step 1: Add `sms` to dependencies and register new data files in manifest**

Edit `repair_custom/__manifest__.py`:

```python
'depends': ['repair_devices', 'web', 'stock', 'sale_management', 'account', 'sms'],
```

In the `'data'` list, add (after `'data/cron_data.xml'`):

```python
'data/review_sms_template_data.xml',
'data/review_sms_cron_data.xml',
```

- [ ] **Step 2: Import the new model module**

Read `repair_custom/models/__init__.py` first to find the import order pattern, then add at the end:

```python
from . import repair_review_sms
```

- [ ] **Step 3: Create the empty model file**

Create `repair_custom/models/repair_review_sms.py` with:

```python
# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class RepairOrder(models.Model):
    _inherit = 'repair.order'
```

- [ ] **Step 4: Commit**

```bash
git add repair_custom/__manifest__.py repair_custom/models/__init__.py repair_custom/models/repair_review_sms.py
git commit -m "feat(review-sms): scaffold repair_review_sms module and sms dependency"
```

---

## Task 2: Add fields to `repair.order`

**Files:**
- Modify: `repair_custom/models/repair_review_sms.py`

- [ ] **Step 1: Add the four review-SMS fields**

Append inside the `RepairOrder` class:

```python
    review_sms_state = fields.Selection(
        [
            ('none', 'Non applicable'),
            ('pending', 'En attente'),
            ('sent', 'Envoyé'),
            ('cancelled', 'Annulé'),
            ('skipped', 'Ignoré'),
        ],
        string="SMS d'avis Google",
        default='none',
        tracking=True,
        copy=False,
    )
    review_sms_eligible_date = fields.Datetime(
        string="SMS d'avis - date prévue",
        copy=False,
    )
    review_sms_sent_date = fields.Datetime(
        string="SMS d'avis - date d'envoi",
        copy=False,
    )
    review_sms_skip_reason = fields.Char(
        string="SMS d'avis - motif d'omission",
        copy=False,
    )
```

- [ ] **Step 2: Restart Odoo with module update and verify fields exist**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

Expected: clean exit code 0, no traceback.

- [ ] **Step 3: Commit**

```bash
git add repair_custom/models/repair_review_sms.py
git commit -m "feat(review-sms): add review SMS state fields on repair.order"
```

---

## Task 3: Configuration fields in `res.config.settings`

**Files:**
- Modify: `repair_custom/models/res_config_settings.py`
- Modify: `repair_custom/views/res_config_settings_views.xml`

- [ ] **Step 1: Add three config fields**

Append to the `ResConfigSettings` class in `repair_custom/models/res_config_settings.py`:

```python
    review_sms_delay_days = fields.Integer(
        string="Délai avant SMS d'avis Google (jours)",
        config_parameter='repair_custom.review_sms_delay_days',
        default=7,
        help="Nombre de jours après la livraison avant l'envoi automatique du SMS d'avis Google.",
    )
    review_sms_dedup_months = fields.Integer(
        string="Délai anti-doublon SMS d'avis (mois)",
        config_parameter='repair_custom.review_sms_dedup_months',
        default=6,
        help="Ne pas renvoyer de SMS d'avis si le client en a reçu un dans les X derniers mois.",
    )
    review_sms_template_id = fields.Many2one(
        'sms.template',
        string="Modèle SMS d'avis Google",
        config_parameter='repair_custom.review_sms_template_id',
        domain=[('model', '=', 'repair.order')],
        help="Modèle utilisé pour le SMS d'avis. Modifier le corps pour mettre à jour le lien Google Review.",
    )
```

- [ ] **Step 2: Add the settings block in the view**

Read `repair_custom/views/res_config_settings_views.xml` first to find the existing settings layout pattern (look for `<block>` or `<setting>` tags). Then add a new settings block following the same pattern, containing the three fields above. Title: "SMS d'avis Google".

If the file uses Odoo 17's `<setting>` style, the block looks like:

```xml
<block title="SMS d'avis Google" id="review_sms_settings">
    <setting id="review_sms_delay" string="Délai avant SMS d'avis Google (jours)">
        <field name="review_sms_delay_days"/>
    </setting>
    <setting id="review_sms_dedup" string="Délai anti-doublon SMS d'avis (mois)">
        <field name="review_sms_dedup_months"/>
    </setting>
    <setting id="review_sms_template" string="Modèle SMS d'avis Google">
        <field name="review_sms_template_id"/>
    </setting>
</block>
```

If the file uses a different layout, mirror the surrounding pattern instead.

- [ ] **Step 3: Restart Odoo with module update**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

Expected: clean exit, no XML parse errors.

- [ ] **Step 4: Commit**

```bash
git add repair_custom/models/res_config_settings.py repair_custom/views/res_config_settings_views.xml
git commit -m "feat(review-sms): expose review SMS delay/dedup/template in settings"
```

---

## Task 4: SMS template data fixture

**Files:**
- Create: `repair_custom/data/review_sms_template_data.xml`

- [ ] **Step 1: Write the template fixture**

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <data noupdate="1">
        <record id="sms_template_review_request" model="sms.template">
            <field name="name">Demande d'avis Google après livraison</field>
            <field name="model_id" ref="model_repair_order"/>
            <field name="body">Bonjour {{ object.partner_id.name }}, merci de nous avoir confié votre {{ object.device_id.display_name }}. Votre avis compte beaucoup pour nous : [LIEN_GOOGLE_REVIEW]</field>
        </record>
    </data>
</odoo>
```

Note: `[LIEN_GOOGLE_REVIEW]` is a placeholder the admin must replace via *Technical → SMS Templates* with the real Google review URL after install.

- [ ] **Step 2: Restart Odoo with module update and verify template appears**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

Expected: clean exit. Template can be seen in *Settings → Technical → SMS → SMS Templates* in the running app.

- [ ] **Step 3: Commit**

```bash
git add repair_custom/data/review_sms_template_data.xml
git commit -m "feat(review-sms): add Google review request SMS template"
```

---

## Task 5: Test fixture extension

**Files:**
- Create: `repair_custom/tests/test_review_sms.py`

- [ ] **Step 1: Write the test scaffold and partner fixture with mobile**

```python
# -*- coding: utf-8 -*-
from unittest.mock import patch
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'review_sms')
class ReviewSmsCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Repair = cls.env['repair.order']
        cls.Partner = cls.env['res.partner']

        cls.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.review_sms_delay_days', '7')
        cls.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.review_sms_dedup_months', '6')

        cls.partner_with_mobile = cls.Partner.create({
            'name': 'Client Avec Mobile',
            'mobile': '+33611112222',
        })
        cls.partner_without_phone = cls.Partner.create({
            'name': 'Client Sans Téléphone',
        })

        # Make sure default template is referenced in config
        template = cls.env.ref('repair_custom.sms_template_review_request')
        cls.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.review_sms_template_id', str(template.id))
        cls.template = template

    def _make_delivered_repair(self, partner=None):
        """Create a repair and force-set it to delivered for testing."""
        partner = partner or self.partner_with_mobile
        repair = self.Repair.create({'partner_id': partner.id})
        # Bypass workflow: directly write delivery_state to trigger our hook
        repair.write({'delivery_state': 'delivered'})
        return repair

    def test_placeholder(self):
        self.assertTrue(self.template)
```

- [ ] **Step 2: Run the placeholder test to verify the harness works**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --test-tags review_sms --stop-after-init
```

Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add repair_custom/tests/test_review_sms.py
git commit -m "test(review-sms): add test scaffold for review SMS feature"
```

---

## Task 6: Schedule SMS on delivery (write override)

**Files:**
- Modify: `repair_custom/models/repair_review_sms.py`
- Modify: `repair_custom/tests/test_review_sms.py`

- [ ] **Step 1: Write failing tests for the schedule-on-delivery behavior**

Replace `test_placeholder` in `test_review_sms.py` with:

```python
    def test_pending_on_delivery_with_mobile(self):
        repair = self._make_delivered_repair()
        self.assertEqual(repair.review_sms_state, 'pending')
        self.assertTrue(repair.review_sms_eligible_date)
        # eligible date roughly = now + 7 days
        delta = repair.review_sms_eligible_date - fields.Datetime.now()
        self.assertGreater(delta.total_seconds(), 6 * 86400)
        self.assertLess(delta.total_seconds(), 8 * 86400)

    def test_skipped_on_delivery_without_phone(self):
        repair = self._make_delivered_repair(partner=self.partner_without_phone)
        self.assertEqual(repair.review_sms_state, 'skipped')
        self.assertEqual(repair.review_sms_skip_reason, "Pas de numéro mobile")

    def test_skipped_on_delivery_when_dedup_window_matches(self):
        # First repair already sent recently
        prior = self._make_delivered_repair()
        prior.write({
            'review_sms_state': 'sent',
            'review_sms_sent_date': fields.Datetime.now() - relativedelta(months=1),
        })
        # New repair for same partner
        new_repair = self.Repair.create({'partner_id': self.partner_with_mobile.id})
        new_repair.write({'delivery_state': 'delivered'})
        self.assertEqual(new_repair.review_sms_state, 'skipped')
        self.assertEqual(new_repair.review_sms_skip_reason, "SMS envoyé récemment au client")

    def test_revert_delivery_resets_pending(self):
        repair = self._make_delivered_repair()
        self.assertEqual(repair.review_sms_state, 'pending')
        repair.write({'delivery_state': 'none'})
        self.assertEqual(repair.review_sms_state, 'none')
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --test-tags review_sms --stop-after-init
```

Expected: failures — `review_sms_state` stays `none` after delivery.

- [ ] **Step 3: Implement the write override and helpers**

Append inside the `RepairOrder` class in `repair_custom/models/repair_review_sms.py`:

```python
    SKIP_REASON_NO_PHONE = "Pas de numéro mobile"
    SKIP_REASON_DEDUP = "SMS envoyé récemment au client"

    def _get_review_sms_delay_days(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_custom.review_sms_delay_days', 7))

    def _get_review_sms_dedup_months(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_custom.review_sms_dedup_months', 6))

    def _has_review_sms_phone(self):
        self.ensure_one()
        return bool(self.partner_id.mobile or self.partner_id.phone)

    def _review_sms_recently_sent(self):
        self.ensure_one()
        months = self._get_review_sms_dedup_months()
        cutoff = fields.Datetime.now() - relativedelta(months=months)
        return bool(self.env['repair.order'].search_count([
            ('id', '!=', self.id),
            ('partner_id', '=', self.partner_id.id),
            ('review_sms_sent_date', '>=', cutoff),
        ]))

    def _schedule_review_sms(self):
        """Called when delivery_state flips to 'delivered'. Decides
        whether to enqueue, skip, or do nothing."""
        for rec in self:
            if rec.review_sms_state not in ('none', 'cancelled'):
                continue
            if not rec._has_review_sms_phone():
                rec.write({
                    'review_sms_state': 'skipped',
                    'review_sms_skip_reason': rec.SKIP_REASON_NO_PHONE,
                })
                continue
            if rec._review_sms_recently_sent():
                rec.write({
                    'review_sms_state': 'skipped',
                    'review_sms_skip_reason': rec.SKIP_REASON_DEDUP,
                })
                continue
            eligible = fields.Datetime.now() + relativedelta(
                days=rec._get_review_sms_delay_days())
            rec.write({
                'review_sms_state': 'pending',
                'review_sms_eligible_date': eligible,
                'review_sms_skip_reason': False,
            })

    def write(self, vals):
        if 'delivery_state' not in vals:
            return super().write(vals)
        # Snapshot per-record previous state so we can detect transitions
        prev = {rec.id: rec.delivery_state for rec in self}
        res = super().write(vals)
        new_state = vals['delivery_state']
        for rec in self:
            old_state = prev.get(rec.id)
            if old_state != 'delivered' and new_state == 'delivered':
                rec._schedule_review_sms()
            elif old_state == 'delivered' and new_state != 'delivered' \
                    and rec.review_sms_state == 'pending':
                rec.write({
                    'review_sms_state': 'none',
                    'review_sms_eligible_date': False,
                })
        return res
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --test-tags review_sms --stop-after-init
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add repair_custom/models/repair_review_sms.py repair_custom/tests/test_review_sms.py
git commit -m "feat(review-sms): schedule review SMS on delivery_state transition"
```

---

## Task 7: Cron implementation (sending logic)

**Files:**
- Create: `repair_custom/data/review_sms_cron_data.xml`
- Modify: `repair_custom/models/repair_review_sms.py`
- Modify: `repair_custom/tests/test_review_sms.py`

- [ ] **Step 1: Write the cron data file**

Create `repair_custom/data/review_sms_cron_data.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <data noupdate="1">
        <record id="ir_cron_send_review_sms" model="ir.cron">
            <field name="name">Réparation : envoyer les SMS d'avis Google</field>
            <field name="model_id" ref="model_repair_order"/>
            <field name="state">code</field>
            <field name="code">model._cron_send_review_sms()</field>
            <field name="interval_number">1</field>
            <field name="interval_type">hours</field>
            <field name="numbercall">-1</field>
            <field name="active" eval="True"/>
        </record>
    </data>
</odoo>
```

- [ ] **Step 2: Write failing tests for the cron**

Append to `test_review_sms.py`:

```python
    def _force_eligible(self, repair, when=None):
        repair.write({
            'review_sms_eligible_date': when or (fields.Datetime.now() - relativedelta(minutes=1)),
        })

    def test_cron_skips_not_yet_eligible(self):
        repair = self._make_delivered_repair()
        repair.write({
            'review_sms_eligible_date': fields.Datetime.now() + relativedelta(days=1),
        })
        with patch.object(type(self.template), 'send_sms') as mock_send:
            self.Repair._cron_send_review_sms()
            mock_send.assert_not_called()
        self.assertEqual(repair.review_sms_state, 'pending')

    def test_cron_sends_eligible_repair(self):
        repair = self._make_delivered_repair()
        self._force_eligible(repair)
        with patch.object(type(self.template), 'send_sms') as mock_send:
            self.Repair._cron_send_review_sms()
            mock_send.assert_called_once_with(repair.id)
        self.assertEqual(repair.review_sms_state, 'sent')
        self.assertTrue(repair.review_sms_sent_date)

    def test_cron_dedup_recheck_at_send_time(self):
        # Eligible repair, but a sibling got sent in between
        repair = self._make_delivered_repair()
        self._force_eligible(repair)
        sibling = self.Repair.create({'partner_id': self.partner_with_mobile.id})
        sibling.write({
            'delivery_state': 'delivered',
            'review_sms_state': 'sent',
            'review_sms_sent_date': fields.Datetime.now(),
        })
        with patch.object(type(self.template), 'send_sms') as mock_send:
            self.Repair._cron_send_review_sms()
            mock_send.assert_not_called()
        self.assertEqual(repair.review_sms_state, 'skipped')
        self.assertEqual(repair.review_sms_skip_reason, "SMS envoyé récemment au client")

    def test_cron_send_failure_keeps_pending(self):
        repair = self._make_delivered_repair()
        self._force_eligible(repair)
        with patch.object(type(self.template), 'send_sms', side_effect=Exception("IAP fail")):
            self.Repair._cron_send_review_sms()
        self.assertEqual(repair.review_sms_state, 'pending')
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --test-tags review_sms --stop-after-init
```

Expected: failures — `_cron_send_review_sms` doesn't exist.

- [ ] **Step 4: Implement the cron and template lookup**

Append inside the `RepairOrder` class:

```python
    @api.model
    def _get_review_sms_template(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'repair_custom.review_sms_template_id')
        if param:
            template = self.env['sms.template'].browse(int(param)).exists()
            if template:
                return template
        return self.env.ref(
            'repair_custom.sms_template_review_request',
            raise_if_not_found=False)

    @api.model
    def _cron_send_review_sms(self):
        template = self._get_review_sms_template()
        if not template:
            _logger.warning("Review SMS template not configured, skipping cron")
            return
        repairs = self.search([
            ('review_sms_state', '=', 'pending'),
            ('review_sms_eligible_date', '<=', fields.Datetime.now()),
        ])
        for repair in repairs:
            if not repair._has_review_sms_phone():
                repair.write({
                    'review_sms_state': 'skipped',
                    'review_sms_skip_reason': repair.SKIP_REASON_NO_PHONE,
                })
                continue
            if repair._review_sms_recently_sent():
                repair.write({
                    'review_sms_state': 'skipped',
                    'review_sms_skip_reason': repair.SKIP_REASON_DEDUP,
                })
                continue
            try:
                template.send_sms(repair.id)
                repair.write({
                    'review_sms_state': 'sent',
                    'review_sms_sent_date': fields.Datetime.now(),
                })
                repair.message_post(body=_("SMS d'avis Google envoyé."))
            except Exception as e:
                _logger.exception(
                    "Review SMS send failed for repair %s", repair.id)
                repair.message_post(
                    body=_("Échec envoi SMS d'avis : %s") % e)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --test-tags review_sms --stop-after-init
```

Expected: 8 passed total.

- [ ] **Step 6: Commit**

```bash
git add repair_custom/data/review_sms_cron_data.xml repair_custom/models/repair_review_sms.py repair_custom/tests/test_review_sms.py
git commit -m "feat(review-sms): add hourly cron to send eligible review SMS via IAP"
```

---

## Task 8: Cancel button + status group on the form view

**Files:**
- Modify: `repair_custom/models/repair_review_sms.py`
- Modify: `repair_custom/views/repair_views.xml`
- Modify: `repair_custom/tests/test_review_sms.py`

- [ ] **Step 1: Write failing test for the cancel action**

Append to `test_review_sms.py`:

```python
    def test_cancel_action_marks_cancelled(self):
        repair = self._make_delivered_repair()
        self.assertEqual(repair.review_sms_state, 'pending')
        repair.action_cancel_review_sms()
        self.assertEqual(repair.review_sms_state, 'cancelled')

    def test_cancel_then_cron_skips(self):
        repair = self._make_delivered_repair()
        self._force_eligible(repair)
        repair.action_cancel_review_sms()
        with patch.object(type(self.template), 'send_sms') as mock_send:
            self.Repair._cron_send_review_sms()
            mock_send.assert_not_called()
        self.assertEqual(repair.review_sms_state, 'cancelled')
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --test-tags review_sms --stop-after-init
```

Expected: failure — `action_cancel_review_sms` does not exist.

- [ ] **Step 3: Implement the action**

Append inside the `RepairOrder` class:

```python
    def action_cancel_review_sms(self):
        for rec in self:
            if rec.review_sms_state != 'pending':
                continue
            rec.write({'review_sms_state': 'cancelled'})
            rec.message_post(body=_("SMS d'avis Google annulé manuellement."))
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --test-tags review_sms --stop-after-init
```

Expected: 10 passed total.

- [ ] **Step 5: Add the button and status group in the form view**

Open `repair_custom/views/repair_views.xml`. Find the main `repair.order` form view. In the `<header>` block, add the cancel button alongside existing buttons:

```xml
<button name="action_cancel_review_sms"
        type="object"
        string="Annuler SMS d'avis"
        attrs="{'invisible': [('review_sms_state', '!=', 'pending')]}"/>
```

In the form's right-side info group (find an existing `<group>` that holds delivery/end-date info; look near `delivery_state`), add a sub-group:

```xml
<group string="SMS d'avis Google" name="review_sms_group">
    <field name="review_sms_state" widget="badge"
           decoration-success="review_sms_state == 'sent'"
           decoration-info="review_sms_state == 'pending'"
           decoration-muted="review_sms_state in ('cancelled', 'skipped', 'none')"/>
    <field name="review_sms_eligible_date"
           attrs="{'invisible': [('review_sms_state', '!=', 'pending')]}"/>
    <field name="review_sms_sent_date"
           attrs="{'invisible': [('review_sms_state', '!=', 'sent')]}"/>
    <field name="review_sms_skip_reason"
           attrs="{'invisible': [('review_sms_state', '!=', 'skipped')]}"/>
</group>
```

- [ ] **Step 6: Restart Odoo with module update to validate the view XML**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --stop-after-init
```

Expected: clean exit, no view validation errors.

- [ ] **Step 7: Commit**

```bash
git add repair_custom/models/repair_review_sms.py repair_custom/views/repair_views.xml repair_custom/tests/test_review_sms.py
git commit -m "feat(review-sms): add cancel button and status indicator on repair form"
```

---

## Task 9: Manual end-to-end smoke test

**Files:** None (manual verification)

- [ ] **Step 1: Start Odoo in dev mode**

```bash
workon odoo_dev && cd /Users/martin/Documents/odoo_dev/odoo && ./odoo-bin -c ../odoo.conf -u repair_custom --dev=reload,xml
```

- [ ] **Step 2: Verify settings UI**

Navigate to *Settings → General Settings*, find the "SMS d'avis Google" block, set delay to 0 (so SMS is immediately eligible), confirm dedup months default to 6.

- [ ] **Step 3: Verify template exists and edit Google review URL**

*Settings → Technical → SMS → SMS Templates*. Open "Demande d'avis Google après livraison", replace `[LIEN_GOOGLE_REVIEW]` with a real placeholder URL like `https://g.page/r/EXAMPLE/review`, save.

- [ ] **Step 4: Verify schedule on delivery and cancel button**

Create a test repair with a partner that has a mobile number, walk it through to delivered (or use the developer mode to set `delivery_state` directly). On the form, confirm "SMS d'avis Google" group shows state `pending` with the eligible date. The "Annuler SMS d'avis" button should be visible in the header. Click it and confirm state becomes `cancelled`, button disappears, chatter shows the cancellation log.

- [ ] **Step 5: Verify cron runs**

In *Settings → Technical → Scheduled Actions*, find "Réparation : envoyer les SMS d'avis Google" and click "Run Manually". Confirm a pending repair (with delay=0) transitions to `sent` and the chatter shows "SMS d'avis Google envoyé." (Without IAP credits the send_sms call may raise — that's expected; the test verifies the integration path, not the gateway.)

- [ ] **Step 6: Final commit if any tweaks were needed**

If any changes were made during smoke testing, commit them. Otherwise this task has no commit.

---

## Verification checklist

- [ ] All 10 tests pass: `--test-tags review_sms`
- [ ] Module installs cleanly with `-u repair_custom`
- [ ] Settings show three new fields with French labels
- [ ] SMS template visible in Technical menu, editable
- [ ] Cron registered as "Réparation : envoyer les SMS d'avis Google", interval 1 hour
- [ ] Delivered repair shows `pending` state with correct eligible date
- [ ] Cancel button visible only when `pending`, transitions to `cancelled`
- [ ] Reverting `delivery_state` resets `pending` → `none`
- [ ] Partner without mobile/phone gets `skipped` with reason
- [ ] Dedup window respected on both schedule and send
