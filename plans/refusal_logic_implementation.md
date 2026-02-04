# Quotation Refusal Logic Implementation - Technical Specification

## Overview
This document outlines the implementation of integrated quotation refusal functionality in the `repair.order` model, leveraging existing infrastructure without requiring a separate wizard.

## Current State Analysis
- `quote_state` field exists with 'refused' option but no implementation
- Uses `repair.pricing.wizard` for quote creation
- Basic sale order linking exists but lacks synchronization
- Existing state management infrastructure available

## Implementation Plan

### 1. Model Enhancements

#### Add Refusal Methods to Repair Order Model
```python
# In repair_custom/models/repair_order.py

def action_refuse_quote(self):
    """Mark quotation as refused with reason capture."""
    self.ensure_one()
    reason = self.env.context.get('refusal_reason', '')
    
    # Update quote state
    self.write({
        'quote_state': 'refused',
        'refusal_reason': reason,
        'refusal_date': fields.Datetime.now(),
        'refusal_user_id': self.env.user.id,
    })
    
    # Log refusal activity
    self._log_refusal_activity(reason)
    
    # Update related sale order if exists
    if self.sale_order_id:
        self._update_related_sale_order_on_refusal()
    
    return True

def _log_refusal_activity(self, reason):
    """Log refusal activity for audit trail."""
    activity_type = self.env.ref('repair_custom.mail_act_repair_quote_refused', raise_if_not_found=True)
    self.activity_schedule(
        activity_type_id=activity_type.id,
        user_id=self.env.user.id,
        summary="Devis Refusé",
        note=f"Raison: {reason}\nRéparateur: {self.env.user.name}",
        date_deadline=fields.Date.today(),
    )

def _update_related_sale_order_on_refusal(self):
    """Update linked sale order when quote is refused."""
    if self.sale_order_id and self.sale_order_id.state not in ['cancel', 'done']:
        self.sale_order_id.write({
            'state': 'cancel',
            'refusal_reason': self.refusal_reason,
        })
```

#### Add New Fields for Refusal Tracking
```python
# In repair_custom/models/repair_order.py

refusal_reason = fields.Text("Raison du refus", readonly=True, copy=False)
refusal_date = fields.Datetime("Date du refus", readonly=True, copy=False)
refusal_user_id = fields.Many2one('res.users', "Utilisateur refus", readonly=True, copy=False)
```

### 2. State Machine Updates

#### Enhanced State Transitions
```python
# Update existing state transition logic
def action_manager_validate_quote(self):
    """Manager validates quote."""
    self.ensure_one()
    
    # Clear any existing refusal state before approval
    if self.quote_state == 'refused':
        self.write({
            'quote_state': 'pending',
            'refusal_reason': False,
            'refusal_date': False,
            'refusal_user_id': False,
        })
    
    # Existing validation logic
    target_type_id = self.env.ref('repair_custom.mail_act_repair_quote_validate').id
    activities = self.activity_ids.filtered(lambda a: a.activity_type_id.id == target_type_id)
    if activities:
        activities.action_feedback(feedback=f"Validé par {self.env.user.name}")
    
    self.message_post(body="Devis validé par le management.")
    return self.write({'quote_state': 'approved'})

def action_refuse_quote_wizard(self):
    """Wizard action for refusal with reason capture."""
    self.ensure_one()
    return {
        'name': _("Refuser le Devis"),
        'type': 'ir.actions.act_window',
        'res_model': 'repair.order',
        'view_mode': 'form',
        'target': 'new',
        'context': {
            'default_refusal_reason': '',
            'default_action': 'refuse_quote',
        },
    }
```

### 3. Two-way Synchronization

#### Sale Order Event Listener
```python
# In repair_custom/models/repair_order.py

@api.model
def _register_hook(self):
    """Register sale order state change listener."""
    sale_order = self.env['sale.order']
    sale_order._register_hook('state_change', self._on_sale_order_state_change)

def _on_sale_order_state_change(self, sale_order):
    """Handle sale order state changes affecting repair quotes."""
    repairs = self.search([('sale_order_id', '=', sale_order.id)])
    if sale_order.state == 'cancel' and repairs:
        repairs.action_refuse_quote()
```

### 4. UI Integration Points

#### Refusal Action in Repair Form
```xml
<!-- In repair_custom/views/repair_views.xml -->
<field name="quote_state" widget="statusbar" statusbar_visible="draft,pending,approved,refused"/>
<button name="action_refuse_quote" type="object" string="Refuser le Devis" 
        attrs="{'invisible': [('quote_state', 'not in', ('pending', 'approved'))]}"/>
<field name="refusal_reason" readonly="1" string="Raison du refus"/>
<field name="refusal_date" readonly="1" string="Date du refus"/>
<field name="refusal_user_id" readonly="1" string="Refusé par"/>
```

### 5. Data Integrity & Validation

#### Refusal Constraints
```python
# In repair_custom/models/repair_order.py

@api.constrains('quote_state', 'sale_order_id')
def _check_refusal_consistency(self):
    """Ensure consistency between quote state and sale order state."""
    for rec in self:
        if rec.sale_order_id and rec.quote_state == 'refused':
            if rec.sale_order_id.state != 'cancel':
                raise ValidationError(
                    _("L'état du devis refusé doit correspondre à l'état 'Annulé' du bon de commande.")
                )
```

## Implementation Steps

1. **Add refusal tracking fields** to `repair.order` model
2. **Implement refusal methods** with state transitions
3. **Add sale order synchronization** logic
4. **Update state machine** for refusal scenarios
5. **Integrate UI elements** in repair views
6. **Add validation constraints** for data integrity

## Benefits of This Approach

- **No separate wizard needed** - integrates directly into existing workflow
- **Leverages existing infrastructure** - uses current state management
- **Maintains data consistency** - two-way synchronization
- **Audit trail built-in** - tracks refusal reasons and users
- **Seamless user experience** - integrated into repair form

This implementation provides a complete refusal mechanism that works within the existing system architecture while maintaining all current functionality.