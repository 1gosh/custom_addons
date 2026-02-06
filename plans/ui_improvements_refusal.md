# UI Improvements for Refusal Actions - Technical Specification

## Overview
This document outlines the UI enhancements needed to integrate quotation refusal functionality into the repair order interface, providing clear visual feedback and action buttons.

## Current UI Analysis
- Repair form views exist but lack refusal-specific UI elements
- Status indicators for quote states are minimal
- No dedicated refusal action buttons
- Limited visibility of refusal information

## Implementation Plan

### 1. Enhanced Status Bar for Quote States

#### Updated Repair Form View
```xml
<!-- In repair_custom/views/repair_views.xml -->
<record id="view_repair_order_form" model="ir.ui.view">
    <field name="name">repair.order.form</field>
    <field name="model">repair.order</field>
    <field name="arch" type="xml">
        <form string="Repair Order">
            <sheet>
                <!-- Existing fields -->
                
                <!-- Enhanced Quote Status Section -->
                <div class="o_statusbar" style="margin-bottom: 15px;">
                    <field name="quote_state" widget="statusbar" 
                           statusbar_visible="none,draft,pending,approved,refused"
                           statusbar_colors="{'refused': 'danger'}"/>
                </div>
                
                <!-- Refusal Action Buttons -->
                <div class="oe_button_box" style="margin-bottom: 15px;">
                    <button name="action_refuse_quote" type="object" 
                            string="Refuser le Devis" class="btn btn-danger"
                            attrs="{'invisible': [('quote_state', 'not in', ('pending', 'approved'))]}"/>
                    <button name="action_manager_validate_quote" type="object" 
                            string="Valider le Devis" class="btn btn-success"
                            attrs="{'invisible': [('quote_state', 'not in', ('pending', 'draft'))]}"/>
                </div>
                
                <!-- Refusal Information Display -->
                <div class="oe_chatter">
                    <div class="o_form_sheet">
                        <!-- Existing fields continue -->
                        
                        <!-- Refusal Details Section -->
                        <div class="o_group" attrs="{'invisible': [('quote_state', '!=', 'refused')]}">
                            <h4>Informations de Refus</h4>
                            <div class="o_row">
                                <div class="o_form_group">
                                    <field name="refusal_reason" readonly="1" string="Raison du refus"/>
                                </div>
                                <div class="o_form_group">
                                    <field name="refusal_date" readonly="1" string="Date du refus"/>
                                </div>
                            </div>
                            <div class="o_form_group">
                                <field name="refusal_user_id" readonly="1" string="Refusé par"/>
                            </div>
                        </div>
                    </div>
                </div>
            </sheet>
        </form>
    </field>
</record>
```

### 2. Enhanced List View Indicators

#### Repair List View Updates
```xml
<!-- In repair_custom/views/repair_views.xml -->
<record id="view_repair_order_tree" model="ir.ui.view">
    <field name="name">repair.order.tree</field>
    <field name="model">repair.order</field>
    <field name="arch" type="xml">
        <tree string="Repair Orders" decoration-info="state=='done'" decoration-danger="state=='irreparable' or state=='cancel'">
            <!-- Existing fields -->
            
            <!-- Quote State Column with Color Coding -->
            <field name="quote_state" string="Statut Devis" widget="statusbar"
                   statusbar_visible="none,draft,pending,approved,refused"
                   statusbar_colors="{'refused': 'danger'}"/>
            
            <!-- Quick Action Buttons -->
            <button name="action_refuse_quote" type="object" string="Refuser" 
                    class="oe_stat_button" icon="fa-times"
                    attrs="{'invisible': [('quote_state', 'not in', ('pending', 'approved'))]}"/>
            <button name="action_manager_validate_quote" type="object" string="Valider" 
                    class="oe_stat_button" icon="fa-check"
                    attrs="{'invisible': [('quote_state', 'not in', ('pending', 'draft'))]}"/>
        </tree>
    </field>
</record>
```

### 3. Sale Order Integration Panel

#### Enhanced SO Linking Section
```xml
<!-- In repair_custom/views/repair_views.xml -->
<record id="view_repair_order_form_so_panel" model="ir.ui.view">
    <field name="name">repair.order.form.so.panel</field>
    <field name="model">repair.order</field>
    <field name="arch" type="xml">
        <form string="Repair Order">
            <sheet>
                <!-- Existing fields -->
                
                <!-- Sale Order Integration Panel -->
                <div class="o_group" attrs="{'invisible': [('sale_order_id', '=', False)]}">
                    <h4>Bon de Commande Lié</h4>
                    <div class="o_row">
                        <div class="o_form_group">
                            <field name="sale_order_id" widget="many2one_button"/>
                        </div>
                        <div class="o_form_group">
                            <field name="sale_order_count" string="Nombre de devis/BC"/>
                        </div>
                    </div>
                    
                    <!-- SO Status Sync -->
                    <div class="o_form_group" attrs="{'invisible': [('sale_order_id', '=', False)]}">
                        <label string="Statut du BC"/>
                        <field name="sale_order_id.state" readonly="1"/>
                        <field name="sale_order_id.refusal_reason" readonly="1" string="Raison du refus (BC)"/>
                    </div>
                </div>
            </sheet>
        </form>
    </field>
</record>
```

### 4. Refusal Wizard Interface

#### Minimal Refusal Wizard (if needed)
```xml
<!-- In repair_custom/wizard/refusal_wizard_views.xml -->
<record id="view_refusal_wizard_form" model="ir.ui.view">
    <field name="name">refusal.wizard.form</field>
    <field name="model">repair.order</field>
    <field name="arch" type="xml">
        <form string="Refuser le Devis">
            <sheet>
                <div class="alert alert-warning">
                    <strong>Attention:</strong> Cette action marquera le devis comme refusé et annulera le bon de commande associé.
                </div>
                
                <div class="o_group">
                    <div class="o_form_group">
                        <label string="Raison du refus"/>
                        <field name="refusal_reason" placeholder="Expliquez la raison du refus..."/>
                    </div>
                </div>
                
                <div class="oe_button_box">
                    <button string="Confirmer le Refus" type="object" name="action_confirm_refusal" class="btn btn-danger"/>
                    <button string="Annuler" special="cancel" class="btn btn-secondary"/>
                </div>
            </sheet>
        </form>
    </field>
</record>
```

### 5. CSS Enhancements for Visual Feedback

#### Style Improvements
```css
/* In repair_custom/static/src/css/views.css */
.refusal-state {
    background-color: #ffebee;
    border-left: 4px solid #f44336;
    padding: 10px;
    margin: 10px 0;
    border-radius: 4px;
}

.refusal-state strong {
    color: #d32f2f;
}

/* Status bar colors */
.statusbar-danger {
    background-color: #ffebee !important;
    color: #d32f2f !important;
}

/* Button styles */
.btn-refuse {
    background-color: #f44336 !important;
    border-color: #d32f2f !important;
}

.btn-refuse:hover {
    background-color: #d32f2f !important;
    border-color: #b71c1c !important;
}
```

## Implementation Steps

1. **Update repair form views** with refusal action buttons and status indicators
2. **Enhance list views** with quote state column and quick actions
3. **Add sale order integration panel** with status synchronization
4. **Create refusal wizard** (minimal implementation)
5. **Add CSS styles** for visual feedback
6. **Test UI responsiveness** across different screen sizes

## Benefits

- **Clear visual feedback** - Users can easily see quote status at a glance
- **Intuitive actions** - Refusal buttons are prominently displayed when applicable
- **Consistent styling** - Matches existing Odoo design patterns
- **Information visibility** - Refusal details are clearly displayed
- **Seamless integration** - Works within existing UI framework

This UI implementation provides a user-friendly interface for the quotation refusal functionality while maintaining consistency with the existing repair order interface design.