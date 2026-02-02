# Architectural Patterns & Design Conventions

This document describes recurring patterns, design decisions, and coding conventions observed throughout the repair management codebase.

## Model Design Patterns

### 1. Computed Fields with Storage

**Pattern:** Fields derived from other data, stored in database for performance.

```python
@api.depends('source_field')
def _compute_destination(self):
    for rec in self:
        rec.destination = computed_value
```

**Used in:**
- Display names: repair_device.py:45 (brand + model concatenation)
- Hierarchical names: repair_device.py:220 (category path building)
- History data: repair.py:260 (device repair count aggregation)
- Dashboard counts: repair.py:1190 (filtered repair counting)

**Why:** Stored computed fields avoid expensive joins/aggregations in list views and search domains. Trade-off: requires recomputation when dependencies change.

### 2. Onchange Handlers for Smart UX

**Pattern:** Update dependent fields and domains when user changes a form field.

```python
@api.onchange('trigger_field')
def _onchange_logic(self):
    # Clear dependent fields, update domains, show warnings
    self.dependent_field = False
    return {'domain': {'field': [('filter', '=', value)]}}
```

**Used in:**
- Device selection triggers category update: repair.py:301
- Unit selection populates device/variant/serial: repair.py:329
- Partner change clears unit assignment: repair.py:315
- Category filters tag suggestions: repair.py:887

**Why:** Provides immediate feedback and prevents invalid data combinations without round-trip to server.

### 3. Custom Name Search (_name_search)

**Pattern:** Override default name matching to support advanced search behaviors.

```python
@api.model
def _name_search(self, name, args=None, operator='ilike', ...):
    # Split terms, build OR domains, context filtering
    domain = ['|', ('field1', operator, term), ('field2', operator, term)]
    return self._search(domain + args, ...)
```

**Used in:**
- Device search by brand OR model: repair_device.py:80
- Category hierarchical search: repair_device.py:232
- Tag context-aware filtering: repair.py:893

**Why:** Enables Google-like search where "Bang Beogram" matches "Bang & Olufsen Beogram 3000" regardless of word order.

### 4. Related Fields with Historical Storage

**Pattern:** Store snapshot of related data at point in time to preserve history.

```python
previous_field = fields.Many2one(..., related='child.field', store=True)
```

**Used in:**
- repair.py:126 (previous technician)
- repair.py:127 (previous warranty)
- repair.py:132 (previous storage location)

**Why:** When repair completes, these fields freeze current values for historical reporting even if related records change later.

### 5. State Machine with Action Buttons

**Pattern:** Controlled state progression through dedicated action methods.

```python
state = fields.Selection([...], readonly=True)

def action_next_state(self):
    # Validation logic
    self.state = 'next_state'
    # Side effects (activities, emails, logs)
```

**Used in:**
- Repair workflow: repair.py:54-66 (state definitions)
- Actions: repair.py:486 (start), repair.py:496 (done), repair.py:447 (confirm)

**Why:** Enforces business rules, prevents invalid transitions, provides audit trail.

## Wizard (Transient Model) Patterns

### 6. Multi-Step Wizard with default_get

**Pattern:** Initialize wizard from context, process multiple records in batch.

```python
@api.model
def default_get(self, fields_list):
    res = super().default_get(fields_list)
    # Extract context data
    repair_ids = self._context.get('active_ids', [])
    # Initialize form fields
    return res
```

**Used in:**
- repair_pricing_wizard.py:42 (invoice generation from multiple repairs)
- repair_manager.py:14 (bulk field updates)
- repair_device_reclassify.py:1 (device recategorization)

**Why:** Wizards operate on selections from list views, need context to initialize.

### 7. Template Selection Dialogs

**Pattern:** Progressive disclosure for template-based content insertion.

```python
# Step 1: Show available templates
# Step 2: User selects, opens editor with pre-filled content
# Step 3: Content applied to original record
```

**Used in:**
- repair.py:997 (RepairTemplateSelector for notes)
- repair_pricing_wizard.py:1 (invoice template application)

**Why:** Reduces repetitive data entry, ensures consistency in professional communications.

## Data Architecture Patterns

### 8. Hierarchical Categories

**Pattern:** Self-referential parent-child relationships with computed full paths.

```python
parent_id = fields.Many2one('self', ...)
complete_name = fields.Char(compute='_compute_complete_name', store=True)

def _compute_complete_name(self):
    for rec in self:
        names = []
        current = rec
        while current:
            names.insert(0, current.name)
            current = current.parent_id
        rec.complete_name = ' / '.join(names)
```

**Used in:**
- repair_device.py:190 (RepairDeviceCategory)

**Why:** Supports unlimited nesting depth (e.g., "Audio / CD / Lecteur portable"), searchable by full path.

### 9. Many2many Tagging with Dynamic Creation

**Pattern:** Tags with category filtering and on-the-fly creation.

```python
tag_ids = fields.Many2many('tag.model', context={'default_category_id': category_id})

# Tag model filters by category
@api.onchange('category_id')
def _filter_tags(self):
    return {'domain': {'tag_ids': [('category_id', '=', self.category_id)]}}
```

**Used in:**
- repair.py:105 (RepairTags with category filtering)
- repair.py:887 (onchange for tag domain)

**Why:** Allows global tags OR category-specific tags, reduces clutter in tag selection.

### 10. Batch/Folder Grouping

**Pattern:** Parent record aggregates state from children.

```python
child_ids = fields.One2many('child.model', 'parent_id')
state = fields.Selection(compute='_compute_state_from_children')

def _compute_state_from_children(self):
    for rec in self:
        if all(child.state == 'done' for child in rec.child_ids):
            rec.state = 'done'
        elif any(child.state != 'draft' for child in rec.child_ids):
            rec.state = 'in_progress'
```

**Used in:**
- repair.py:1095 (RepairBatch computes state from repairs)

**Why:** Groups multiple related records (e.g., repairs from one customer deposit), shows aggregate status.

## UI/UX Patterns

### 11. Conditional Field Visibility

**Pattern:** Show/hide fields based on computed conditions to reduce clutter.

```python
show_field = fields.Boolean(compute='_compute_show_field')
field = fields.Char(invisible="not show_field")

def _compute_show_field(self):
    for rec in self:
        rec.show_field = (rec.condition_met)
```

**Used in:**
- repair.py:242 (show unit field only when device selected)

**Why:** Progressive disclosure - only show relevant fields for current context.

### 12. Smart Buttons (Stat Buttons)

**Pattern:** Action buttons in form header showing computed counts.

```python
count_field = fields.Integer(compute='_compute_count')

def action_view_related(self):
    return {
        'type': 'ir.actions.act_window',
        'res_model': 'related.model',
        'domain': [('relation_id', '=', self.id)],
        'view_mode': 'tree,form',
    }
```

**Used in:**
- repair.py:782 (view repairs from device unit)
- repair_device.py:115 (view units from device model)

**Why:** Quick navigation to related records, visual indication of relationship counts.

### 13. Dashboard Tiles

**Pattern:** Configurable KPI tiles with filtered counts.

```python
category = fields.Selection([('tile1', 'Label'), ...])
filter_logic = fields.Selection([('all', 'All'), ('assigned', 'Assigned to me')])

def _compute_count(self):
    for tile in self:
        domain = self._build_domain_for_category(tile.category, tile.filter_logic)
        tile.count = self.env['target.model'].search_count(domain)
```

**Used in:**
- repair.py:1119 (AtelierDashboardTile with 7 categories)

**Why:** Personalized workshop dashboard, quick access to work queues.

## Integration Patterns

### 14. Mail Thread Integration

**Pattern:** Inherit mail.thread and mail.activity.mixin for communication tracking.

```python
class Model(models.Model):
    _inherit = ['mail.thread', 'mail.activity.mixin']

    field = fields.Char(tracking=True)  # Track changes in chatter
```

**Used in:**
- repair.py:35 (Repair inherits mail tracking)

**Why:** Automatic logging, email integration, activity scheduling, notification system.

### 15. Invoice/Quote Generation

**Pattern:** Create accounting documents from transient wizard with line distribution.

```python
def action_create_invoice(self):
    # Gather line items from multiple sources
    lines = self._prepare_invoice_lines()
    invoice = self.env['account.move'].create({
        'partner_id': self.partner_id.id,
        'invoice_line_ids': [(0, 0, line) for line in lines],
    })
```

**Used in:**
- repair_pricing_wizard.py:156 (invoice creation)
- repair_pricing_wizard.py:254 (quote creation)

**Why:** Centralizes complex pricing logic, supports templates, handles multiple repairs in batch.

### 16. Public Portal Access

**Pattern:** Unauthenticated routes with token-based access.

```python
@http.route('/public/path/<string:token>', type='http', auth='public')
def public_view(self, token):
    record = request.env['model'].sudo().search([('token', '=', token)])
    if not record:
        raise werkzeug.exceptions.NotFound()
    return request.render('template', {'record': record})
```

**Used in:**
- repair_tracking.py:12 (public repair tracking)

**Why:** Allows customers to check repair status without login, improves customer experience.

## Code Organization Conventions

### 17. Field Declaration Order

Standard order observed:
1. Basic fields (Char, Integer, Boolean)
2. Relational fields (Many2one, One2many, Many2many)
3. Computed fields
4. Related fields
5. Selection fields with states

Example: repair.py:40-200

### 18. Method Naming Conventions

- `_compute_*` - Computed field calculators
- `_onchange_*` - Form interaction handlers
- `action_*` - User-triggered button actions
- `_prepare_*` - Data preparation for create/write
- `_name_search` - Custom search behavior
- `_check_*` - Validation methods

### 19. Multi-record Processing

**Pattern:** Always iterate over self for compatibility with recordsets.

```python
def method(self):
    for record in self:
        record.field = value  # Not self.field = value
```

Used consistently throughout repair.py and repair_device.py.

**Why:** Methods may be called on single record or multiple records (e.g., from list view actions).

## Security Patterns

### 20. Role-Based Access Control

**Pattern:** Three-tier permission model with field-level granularity.

- **Technician:** Read/write assigned records
- **Manager:** Create/modify any record
- **Admin:** Full access including configuration

Defined in:
- repair_custom/security/ir.model.access.csv:1
- repair_custom/security/repair_security.xml:1

**Why:** Matches workshop hierarchy - technicians work on repairs, managers oversee operations, admins configure system.

### 21. Record Rules

**Pattern:** Domain-based access restrictions.

```xml
<record id="rule_id" model="ir.rule">
    <field name="domain_force">[('technician_user_id', '=', user.id)]</field>
</record>
```

Used for employee-filtered dashboard tiles and assignment-based access.

**Why:** Ensures users only see relevant data, enforces data isolation.

## Performance Optimizations

### 22. Stored Computed Fields

Trade-off: Storage space vs. query performance.

**When stored:**
- Frequently searched fields (display_name)
- List view columns (counts, states)
- Report data aggregations

**When not stored:**
- Rarely accessed fields
- Highly dynamic data (current time)

### 23. Domain Pre-filtering

**Pattern:** Use `_search()` with computed domains instead of filtering in Python.

```python
domain = [('field', operator, value)]
return self._search(domain + args, limit=limit, order=order)
```

Used in: repair_device.py:80, repair.py:893

**Why:** Database filtering is orders of magnitude faster than Python filtering.

## Common Anti-patterns Avoided

1. **No SQL injection risk:** All queries use ORM domain syntax
2. **No N+1 queries:** Related fields used instead of loops with searches
3. **No global mutable state:** All state stored in database records
4. **No hardcoded IDs:** Uses XML ID references for data dependencies
5. **No string-based field access:** Uses field name validation

## Module Dependency Strategy

- `repair_devices` has no custom dependencies (only stock, product, hr)
- `repair_custom` depends on repair_devices
- Clear separation: catalog management vs. workflow management
- Both modules can be updated independently

This architecture allows for:
- Reuse of device catalog in other contexts
- Independent testing of each module
- Gradual rollout (devices first, then repair workflow)
