# Google Review SMS — Design

## Goal

Automatically send a Google review request SMS to the customer a configurable number of days after a repair's `delivery_state` becomes `delivered`. Avoid spamming customers who already received one recently. Allow staff to cancel a pending SMS from the repair form.

## Non-goals

- No multi-channel (email, WhatsApp) — SMS only via Odoo IAP
- No A/B testing of messages
- No per-customer opt-out UI in this iteration (template/dedup is enough)
- No analytics dashboard

## SMS gateway

**Odoo IAP.** Configured by the admin in *Settings → General → SMS* (purchase credits). The `sms.template.send_sms()` API routes through IAP automatically — no provider code to write.

## Module placement

Everything lives in `repair_custom`. No new module.

## Data model — `repair.order` extensions

New file: `repair_custom/models/repair_review_sms.py` (inherits `repair.order`).

| Field | Type | Notes |
|---|---|---|
| `review_sms_state` | `Selection([('none','Non applicable'),('pending','En attente'),('sent','Envoyé'),('cancelled','Annulé'),('skipped','Ignoré')])` | Default `none`. Tracked. |
| `review_sms_eligible_date` | `Datetime`, stored, computed from `delivery_date` + `delay_days` config | Used in cron WHERE clause. |
| `review_sms_sent_date` | `Datetime` | Set when SMS actually goes out. Used by dedup window. |
| `review_sms_skip_reason` | `Char` | Free-text reason logged when state becomes `skipped` (no mobile, dedup, etc.). |

### Transition rules

- On write where `delivery_state` flips to `delivered`:
  - If neither `partner_id.mobile` nor `partner_id.phone` is set → `review_sms_state='skipped'`, `skip_reason="Pas de numéro mobile"`
  - Else if `_review_sms_recently_sent()` returns True → `review_sms_state='skipped'`, `skip_reason="SMS envoyé récemment au client"`
  - Else → `review_sms_state='pending'`, compute `review_sms_eligible_date = delivery_date + delay_days`
- On write where `delivery_state` moves away from `delivered` while `review_sms_state='pending'` → reset to `none` (cancel the queued send because the delivery was reverted)

### Helper

```python
def _review_sms_recently_sent(self):
    """True if any other repair for the same partner has a review SMS
    sent within the dedup window."""
    months = int(self.env['ir.config_parameter'].sudo().get_param(
        'repair_custom.review_sms_dedup_months', 6))
    cutoff = fields.Datetime.now() - relativedelta(months=months)
    return bool(self.env['repair.order'].search_count([
        ('id', '!=', self.id),
        ('partner_id', '=', self.partner_id.id),
        ('review_sms_sent_date', '>=', cutoff),
    ]))
```

## Configuration — `res.config.settings`

Extend `repair_custom/models/res_config_settings.py` with three fields:

```python
review_sms_delay_days = fields.Integer(
    string="Délai avant SMS d'avis Google (jours)",
    config_parameter='repair_custom.review_sms_delay_days',
    default=7,
    help="Nombre de jours après la livraison avant l'envoi automatique du SMS d'avis.",
)
review_sms_dedup_months = fields.Integer(
    string="Délai anti-doublon SMS d'avis (mois)",
    config_parameter='repair_custom.review_sms_dedup_months',
    default=6,
    help="Ne pas envoyer de nouveau SMS d'avis si le client en a reçu un dans les X derniers mois.",
)
review_sms_template_id = fields.Many2one(
    'sms.template',
    string="Modèle SMS d'avis Google",
    config_parameter='repair_custom.review_sms_template_id',
    domain=[('model', '=', 'repair.order')],
)
```

UI added in `repair_custom/views/res_config_settings_views.xml` under a new "SMS d'avis Google" block.

## SMS template (data fixture)

`repair_custom/data/review_sms_template_data.xml` with `noupdate="1"`:

```xml
<record id="sms_template_review_request" model="sms.template">
    <field name="name">Demande d'avis Google après livraison</field>
    <field name="model_id" ref="model_repair_order"/>
    <field name="body">Bonjour {{ object.partner_id.name }}, merci de nous avoir confié votre {{ object.device_id.display_name }}. Votre avis compte beaucoup pour nous : [LIEN_GOOGLE_REVIEW]</field>
</record>
```

The Google review URL is **edited directly in the template body** by the admin in *Technical → SMS Templates* (no separate config field — keeps the design surface small, and admins editing the body will naturally update the URL alongside the wording).

The `review_sms_template_id` config field is set to this template by default in a post-init hook (or left blank and the cron falls back to the XML-id reference if unset).

## Cron

`repair_custom/data/ir_cron_data.xml`:

```xml
<record id="ir_cron_review_sms" model="ir.cron">
    <field name="name">Repair: envoyer les SMS d'avis Google</field>
    <field name="model_id" ref="model_repair_order"/>
    <field name="state">code</field>
    <field name="code">model._cron_send_review_sms()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">hours</field>
    <field name="active">True</field>
</record>
```

### Cron implementation (on `repair.order`)

```python
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
        if not (repair.partner_id.mobile or repair.partner_id.phone):
            repair.write({
                'review_sms_state': 'skipped',
                'review_sms_skip_reason': "Pas de numéro mobile",
            })
            continue
        if repair._review_sms_recently_sent():
            repair.write({
                'review_sms_state': 'skipped',
                'review_sms_skip_reason': "SMS envoyé récemment au client",
            })
            continue
        try:
            template.send_sms(repair.id)
            repair.write({
                'review_sms_state': 'sent',
                'review_sms_sent_date': fields.Datetime.now(),
            })
            repair.message_post(body="SMS d'avis Google envoyé.")
        except Exception as e:
            _logger.exception("Review SMS send failed for repair %s", repair.id)
            repair.message_post(body=f"Échec envoi SMS d'avis : {e}")
            # Leave state='pending' so it retries next tick
```

`_get_review_sms_template()` reads the config-parameter ID, falls back to the XML-id reference.

## UI on repair form

In `repair_custom/views/repair_order_views.xml` (existing form):

**Header button** — visible only when `review_sms_state == 'pending'`:

```xml
<button name="action_cancel_review_sms"
        type="object"
        string="Annuler SMS d'avis"
        attrs="{'invisible': [('review_sms_state', '!=', 'pending')]}"/>
```

Action handler:

```python
def action_cancel_review_sms(self):
    self.write({'review_sms_state': 'cancelled'})
    self.message_post(body="SMS d'avis Google annulé manuellement.")
```

**Status indicator** — small read-only group in the side panel (or near delivery info):

- `review_sms_state` (badge widget)
- `review_sms_eligible_date` (visible when `pending`)
- `review_sms_sent_date` (visible when `sent`)
- `review_sms_skip_reason` (visible when `skipped`)

## Error handling

| Scenario | Behavior |
|---|---|
| No mobile or phone on partner | State → `skipped`, reason logged |
| Dedup window match at send time (re-checked) | State → `skipped`, reason logged |
| IAP credits exhausted / `send_sms` raises | State stays `pending`, error in chatter, retry next cron tick |
| Template not configured | Cron logs warning and returns (no work done) |
| `delivery_state` reverted before send | State auto-resets to `none` via the write override |

## Testing

`repair_custom/tests/test_review_sms.py`:

1. `test_pending_on_delivery` — flipping to `delivered` with mobile sets state `pending` and correct eligible date
2. `test_skipped_no_mobile` — flipping to `delivered` without mobile sets state `skipped`
3. `test_skipped_dedup_on_delivery` — sibling repair with recent `review_sms_sent_date` causes new repair to skip immediately
4. `test_cron_respects_eligible_date` — pending repair with future eligible date is not picked up
5. `test_cron_sends_and_marks_sent` — eligible repair gets sent (mock `template.send_sms`) and state → `sent`
6. `test_cron_dedup_recheck` — pending repair becomes ineligible between scheduling and cron tick → `skipped`
7. `test_cancel_button` — cancel action sets state to `cancelled`, cron ignores
8. `test_revert_delivery_resets_state` — moving `delivery_state` away from `delivered` resets pending to `none`
9. `test_send_failure_keeps_pending` — `send_sms` raising leaves state `pending` for retry

## Files touched / created

**Created:**
- `repair_custom/models/repair_review_sms.py`
- `repair_custom/data/review_sms_template_data.xml`
- `repair_custom/data/ir_cron_review_sms_data.xml`
- `repair_custom/tests/test_review_sms.py`
- `docs/superpowers/specs/2026-05-13-review-sms-design.md` (this file)

**Modified:**
- `repair_custom/models/__init__.py` (import new module)
- `repair_custom/models/res_config_settings.py` (3 new fields)
- `repair_custom/views/res_config_settings_views.xml` (new block)
- `repair_custom/views/repair_order_views.xml` (button + status group)
- `repair_custom/__manifest__.py` (add new data files; depend on `sms`)

## Open questions / future work

- Per-customer opt-out flag on `res.partner` (deferred — dedup window covers most cases)
- Track Google review click-through (would require a redirect controller; out of scope)
- Multi-language template body (Odoo `sms.template` already supports translations if needed later)
