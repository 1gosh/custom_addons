# -*- coding: utf-8 -*-
"""Main Repair Order model."""

from datetime import date, datetime, time
from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError, ValidationError
from dateutil.relativedelta import relativedelta
import secrets


class Repair(models.Model):
    """Repair Orders - Main repair workflow management."""

    _name = 'repair.order'
    _description = 'Repair Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, entry_date desc'
    _check_company_auto = True

    # --- BASIC FIELDS ---
    entry_date = fields.Datetime(
        string="Date d'entrée",
        default=lambda self: fields.Datetime.now(),
        help="Date et heure d'entrée de l'appareil."
    )
    end_date = fields.Datetime(string="Date de fin", readonly=True, copy=False)
    device_picture = fields.Image()
    last_action_time = fields.Char(string="Heure", compute='_compute_last_action_time')

    @api.depends('write_date')
    def _compute_last_action_time(self):
        for rec in self:
            if rec.write_date:
                user_tz_dt = fields.Datetime.context_timestamp(self, rec.write_date)
                rec.last_action_time = user_tz_dt.strftime('%H:%M')
            else:
                rec.last_action_time = ""

    @api.model
    def _default_location(self):
        return self.env['repair.pickup.location'].search([('name', '=', 'Boutique')], limit=1).id

    pickup_location_id = fields.Many2one(
        'repair.pickup.location',
        string="Lieu de prise en charge",
        required=True,
        default=_default_location
    )
    import_state = fields.Char("Statut pour l'import")
    notes = fields.Text(string="Notes")
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    technician_user_id = fields.Many2one('res.users', string="Technicien (Utilisateur)", readonly=True)
    technician_employee_id = fields.Many2one('hr.employee', string="Technicien", help="Employé responsable.", index=True)

    user_id = fields.Many2one('res.users', string="Responsible", default=lambda self: self.env.user, check_company=True)
    tracking_token = fields.Char('Tracking Token', default=lambda self: secrets.token_urlsafe(32), readonly=True, copy=False)
    tracking_token_expiry = fields.Datetime('Token Expiry', default=lambda self: fields.Datetime.now() + relativedelta(months=6), readonly=True, copy=False)
    tracking_url = fields.Char('Tracking URL', compute="_compute_tracking_url")

    @api.depends('tracking_token')
    def _compute_tracking_url(self):
        base_url = self.env['ir.config_parameter'].get_param('web.base.url')
        for rec in self:
            rec.tracking_url = f"{base_url}/repair/tracking/{rec.tracking_token}" if rec.tracking_token else False

    name = fields.Char('Référence', default='New', index='trigram', copy=False, required=True, readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True, required=True, index=True, default=lambda self: self.env.company)

    state = fields.Selection([
        ('draft', 'New'),
        ('confirmed', 'Confirmed'),
        ('under_repair', 'Under Repair'),
        ('done', 'Repaired'),
        ('irreparable', 'Non Réparable'),
        ('cancel', 'Cancelled')], string='Status',
        copy=False, default='draft', readonly=True, tracking=True, index=True)

    quote_state = fields.Selection([
        ('none', 'Pas de devis'),
        ('draft', 'Estimation en cours'),
        ('pending', 'Attente Validation'),
        ('approved', 'Validé'),
        ('refused', 'Refusé')
    ], string="Statut Devis", default='none', tracking=True)

    delivery_state = fields.Selection([
        ('none', 'En Atelier'),
        ('delivered', 'Livré au Client'),
        ('abandoned', 'Abandonné')
    ], string="Statut Logistique", default='none', tracking=True, copy=False)

    priority = fields.Selection([('0', 'Normal'), ('1', 'Urgent')], default='0', string="Priority")
    partner_id = fields.Many2one('res.partner', 'Customer', index=True, check_company=True, required=True)

    # --- QUOTE LOGIC ---
    quote_required = fields.Boolean(string="Devis Exigé", default=False, tracking=True)
    quote_threshold = fields.Integer(string="Seuil du devis")
    parts_waiting = fields.Boolean(string="Attente de pièces", default=False, tracking=True)
    diagnostic_notes = fields.Text(string="Diagnostic Technique")

    # --- HISTORY AND WARRANTY MANAGEMENT ---
    has_history = fields.Boolean(compute='_compute_history_data', string="A un historique", store=False)
    history_repair_ids = fields.Many2many('repair.order', compute='_compute_history_data', string="Historique Appareil")
    previous_repair_id = fields.Many2one('repair.order', string="Dernière Réparation", compute='_compute_history_data', store=True)

    repair_warranty = fields.Selection([
        ('aucune', 'Aucune'),
        ('sav', 'SAV'),
        ('sar', 'SAR')],
        string="Garantie",
        default='aucune',
        copy=False
    )

    @api.depends('unit_id')
    def _compute_history_data(self):
        """Batch optimization: query all units at once."""
        records_with_units = self.filtered('unit_id')
        records_without_units = self - records_with_units

        records_without_units.update({
            'history_repair_ids': False,
            'has_history': False,
            'previous_repair_id': False
        })

        if not records_with_units:
            return

        unit_ids = records_with_units.mapped('unit_id').ids
        current_repair_ids = [r.id for r in records_with_units if isinstance(r.id, int)]

        domain = [('unit_id', 'in', unit_ids), ('state', '=', 'done')]
        if current_repair_ids:
            domain.append(('id', 'not in', current_repair_ids))

        all_repairs = self.env['repair.order'].search(domain, order='unit_id, end_date desc, write_date desc')

        repairs_by_unit = {}
        for repair in all_repairs:
            unit_id = repair.unit_id.id
            if unit_id not in repairs_by_unit:
                repairs_by_unit[unit_id] = []
            repairs_by_unit[unit_id].append(repair.id)

        for rec in records_with_units:
            repair_ids = repairs_by_unit.get(rec.unit_id.id, [])
            rec.history_repair_ids = [(6, 0, repair_ids)] if repair_ids else False
            rec.has_history = bool(repair_ids)
            rec.previous_repair_id = repair_ids[0] if repair_ids else False

    def _get_sar_warranty_months(self):
        """Get SAR warranty period in months from config or default to 3."""
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_custom.sar_warranty_months', default='3'
        ))

    def _compute_suggested_warranty(self):
        """Calculate suggested warranty based on previous repair history."""
        sar_months = self._get_sar_warranty_months()

        for rec in self:
            is_sar = False
            if rec.previous_repair_id:
                prev_repair = rec.previous_repair_id
                ref_date = prev_repair.end_date or prev_repair.write_date

                if ref_date:
                    limit_date = ref_date.date() + relativedelta(months=sar_months)
                    current_date = rec.entry_date.date() if rec.entry_date else fields.Date.today()
                    if current_date <= limit_date:
                        is_sar = True

            rec.suggested_warranty = 'sar' if is_sar else 'aucune'

    suggested_warranty = fields.Selection([
        ('aucune', 'Aucune'),
        ('sar', 'SAR (Retour)'),
        ('sav', 'SAV'),
    ], string="Garantie Suggérée", compute='_compute_suggested_warranty', store=False)

    @api.onchange('unit_id', 'entry_date')
    def _onchange_unit_workflow(self):
        """UI updates: Apply suggested warranty and show history popup."""
        if not self.unit_id:
            return

        self._compute_history_data()
        self._compute_suggested_warranty()

        unit_changed = (self.unit_id != self._origin.unit_id)
        if unit_changed or self.repair_warranty != 'sav':
            self.repair_warranty = self.suggested_warranty

        # Show history popup if unit changed
        if unit_changed and self.previous_repair_id:
            prev_repair = self.previous_repair_id
            tech_name = prev_repair.technician_employee_id.name or 'Inconnu'
            prev_date_str = (prev_repair.end_date or prev_repair.write_date).strftime('%d/%m/%Y')

            # Check if SAR
            sar_months = self._get_sar_warranty_months()
            ref_date = prev_repair.end_date or prev_repair.write_date
            is_sar = False
            if ref_date:
                limit_date = ref_date.date() + relativedelta(months=sar_months)
                current_date = self.entry_date.date() if self.entry_date else fields.Date.today()
                if current_date <= limit_date:
                    is_sar = True

            if is_sar:
                return {'warning': {
                    'title': _("Retour Garantie (SAR)"),
                    'message': _("ℹ INFO : Appareil sous garantie jusqu'au %s.\n(Réparé le %s par %s)") % (
                        limit_date.strftime('%d/%m/%Y'), prev_date_str, tech_name
                    )
                }}
            else:
                return {'warning': {
                    'title': _("Hors Garantie"),
                    'message': _("ℹ INFO : Cet appareil a déjà été réparé par %s le %s.\n(Garantie expirée)") % (
                        tech_name, prev_date_str
                    )
                }}

    previous_technician_id = fields.Many2one(
        'hr.employee',
        string="Dernier Technicien",
        related='previous_repair_id.technician_employee_id',
        store=True,
        readonly=True
    )

    previous_end_date = fields.Datetime(
        string="Date fin précédente",
        related='previous_repair_id.end_date',
        store=True,
        readonly=True
    )

    # --- DEVICE FIELDS ---
    category_id = fields.Many2one('repair.device.category', string="Catégorie", check_company=True)
    device_id = fields.Many2one('repair.device', string="Modèle")
    variant_id = fields.Many2one('repair.device.variant', string="Variante")
    variant_ids_available = fields.Many2many('repair.device.variant', compute='_compute_variant_ids_available', store=False)

    @api.depends('device_id', 'device_id.variant_ids')
    def _compute_variant_ids_available(self):
        for rec in self:
            rec.variant_ids_available = rec.device_id.variant_ids if rec.device_id else False

    @api.onchange('device_id')
    def _onchange_device_id_set_category(self):
        self._onchange_device_id_clear_variant()
        if self.device_id and self.device_id.category_id:
            self.category_id = self.device_id.category_id

    serial_number = fields.Char("N° de série")
    unit_id = fields.Many2one('repair.device.unit', readonly=True, index=True)
    device_id_name = fields.Char("Appareil", related="unit_id.device_name", store=True, readonly=True)
    show_unit_field = fields.Boolean(string="Afficher champ unité", compute="_compute_show_unit_field")

    @api.onchange('device_id')
    def _onchange_device_id_clear_variant(self):
        if self.unit_id and self.device_id == self.unit_id.device_id:
            return
        if self.device_id:
            self.variant_id = False
            if self.unit_id:
                self.unit_id = False
                self.serial_number = False

    @api.onchange('category_id')
    def _onchange_category_id(self):
        if self.device_id and self.category_id:
            category_of_device = self.device_id.category_id
            selected_category = self.category_id
            allowed_categories = selected_category + selected_category.search([('id', 'child_of', selected_category.id)])
            if category_of_device.id not in allowed_categories.ids:
                self.device_id = False
                self.variant_id = False

    tag_ids = fields.Many2many('repair.tags', string="Pannes")
    work_time = fields.Float(string="Temps de travail")
    internal_notes = fields.Text("Notes de réparation")
    notes_template_id = fields.Many2one('repair.notes.template', string="Insérer un Gabarit", store=False)

    @api.onchange('notes_template_id')
    def _onchange_notes_template_id(self):
        if self.notes_template_id and self.notes_template_id.template_content:
            new_content = self.notes_template_id.template_content
            if self.internal_notes:
                self.internal_notes += '\n\n---\n\n' + new_content
            else:
                self.internal_notes = new_content
            self.notes_template_id = False

    @api.onchange('unit_id')
    def _onchange_unit_id(self):
        for rec in self:
            if rec.unit_id:
                rec.serial_number = rec.unit_id.serial_number
                rec.device_id = rec.unit_id.device_id
                rec.variant_id = rec.unit_id.variant_id

    def action_open_unit(self):
        self.ensure_one()
        if not self.unit_id:
            raise UserError(_("Aucun appareil n'est associé à cette réparation."))
        action = self.env.ref('repair_devices.action_repair_device_unit').read()[0]
        action.update({
            'views': [(False, 'form')],
            'res_id': self.unit_id.id,
            'target': 'current',
        })
        return action

    @api.depends('unit_id', 'partner_id', 'state')
    def _compute_show_unit_field(self):
        Unit = self.env['repair.device.unit']
        for rec in self:
            show = False
            if rec.state == 'draft':
                has_partner_units = False
                if rec.partner_id:
                    has_partner_units = bool(Unit.search([('partner_id', '=', rec.partner_id.id)], limit=1))
                show = bool(rec.unit_id) or has_partner_units
            rec.show_unit_field = show

    @api.onchange('partner_id')
    def _onchange_partner_clear_unit(self):
        if self.partner_id:
            self.unit_id = False

    # --- BATCH MANAGEMENT ---
    batch_id = fields.Many2one('repair.batch', string="Dossier de Dépôt", readonly=True, index=True, ondelete='restrict')
    batch_count = fields.Integer(compute='_compute_batch_count', string="Autres appareils")

    @api.depends('batch_id')
    def _compute_batch_count(self):
        for rec in self:
            if rec.batch_id:
                domain = [('batch_id', '=', rec.batch_id.id)]
                rec.batch_count = self.env['repair.order'].search_count(domain)
            else:
                rec.batch_count = 0

    def action_add_device_to_batch(self):
        self.ensure_one()
        if not self.batch_id:
            new_batch = self.env['repair.batch'].create({'partner_id': self.partner_id.id})
            self.write({'batch_id': new_batch.id})
            current_batch_id = new_batch.id
        else:
            current_batch_id = self.batch_id.id
        return {
            'type': 'ir.actions.act_window',
            'name': _('Nouvel Appareil (Même Dossier)'),
            'res_model': 'repair.order',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_partner_id': self.partner_id.id,
                'default_pickup_location_id': self.pickup_location_id.id,
                'default_entry_date': self.entry_date,
                'default_batch_id': current_batch_id,
            }
        }

    # --- WRITE OVERRIDE WITH SECURITY ---
    def write(self, vals):
        is_admin = self.env.user.has_group('repair_custom.group_repair_admin')
        is_manager = self.env.user.has_group('repair_custom.group_repair_manager')

        if not is_admin:
            protected_fields = {
                'tracking_token', 'invoice_ids', 'sale_order_id',
                'company_id', 'currency_id'
            }

            for field in protected_fields:
                if field in vals and not is_manager:
                    del vals[field]

        if vals.get('state') == 'draft':
            vals = dict(vals)
            vals.update({'technician_user_id': False, 'technician_employee_id': False})

        return super(Repair, self).write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_confirmed(self):
        repairs_to_cancel = self.filtered(lambda ro: ro.state not in ('draft', 'cancel'))
        repairs_to_cancel.action_repair_cancel()

    # --- STATE TRANSITIONS ---
    def action_repair_cancel(self):
        admin = self.env.user.has_group('repair_custom.group_repair_admin')
        if not admin and any(repair.state == 'done' for repair in self):
            raise UserError(_("Impossible d'annuler une réparation terminée."))
        return self.write({'state': 'cancel'})

    def action_repair_cancel_draft(self):
        if self.filtered(lambda repair: repair.state != 'cancel'):
            self.action_repair_cancel()
        return self.write({'state': 'draft', 'end_date': False})

    def action_repair_done(self):
        """Mark repair as done with proper validation and notifications."""
        # Row-level locking
        for rec in self:
            try:
                self.env.cr.execute("SELECT id FROM repair_order WHERE id=%s FOR UPDATE NOWAIT", (rec.id,))
            except Exception:
                raise UserError(_("La réparation %s est en cours de modification par un autre utilisateur.") % rec.name)

        self.invalidate_recordset(['state', 'quote_state', 'quote_required'])

        # Quote validation
        if self.quote_required and self.quote_state != 'approved' and not self.env.context.get('force_stop'):
            return {
                'name': _("Alerte : Devis non validé"),
                'type': 'ir.actions.act_window',
                'res_model': 'repair.warn.quote.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_repair_id': self.id}
            }

        # State change
        res = self.write({
            'state': 'done',
            'parts_waiting': False,
            'end_date': fields.Datetime.now()
        })

        # Cleanup activities
        quote_act_type = self.env.ref('repair_custom.mail_act_repair_quote_validate', raise_if_not_found=False)
        if quote_act_type:
            activities_to_clean = self.activity_ids.filtered(lambda a: a.activity_type_id.id == quote_act_type.id)
            if activities_to_clean:
                activities_to_clean.action_feedback(feedback="Clôture automatique : Réparation terminée.")

        # Manager notifications (batch optimized)
        pickup_type = self.env.ref('repair_custom.mail_act_repair_done', raise_if_not_found=True)
        group_manager = self.env.ref('repair_custom.group_repair_manager', raise_if_not_found=True)

        if pickup_type and group_manager:
            # Get the ir.model ID for repair.order
            repair_model = self.env['ir.model']._get('repair.order')

            activities_to_create = []
            for rec in self:
                for manager_user in group_manager.users:
                    activities_to_create.append({
                        'res_model_id': repair_model.id,
                        'res_model': 'repair.order',
                        'res_id': rec.id,
                        'activity_type_id': pickup_type.id,
                        'user_id': manager_user.id,
                        'summary': "Appareil Prêt",
                        'note': f"L'appareil {rec.device_id_name} est réparé. À facturer et livrer.",
                        'date_deadline': fields.Date.today(),
                    })

            if activities_to_create:
                self.env['mail.activity'].create(activities_to_create)

        return res

    def action_repair_end(self):
        if self.filtered(lambda repair: repair.state != 'under_repair'):
            raise UserError(_("La réparation doit être en cours pour être terminée."))
        return self.action_repair_done()

    def action_set_irreparable(self):
        return self.write({'state': 'irreparable', 'end_date': fields.Datetime.now()})

    def action_repair_delivered(self):
        """Mark repair as delivered with atomic validation."""
        # Row-level locking
        for rec in self:
            try:
                self.env.cr.execute("SELECT id FROM repair_order WHERE id=%s FOR UPDATE NOWAIT", (rec.id,))
            except Exception:
                raise UserError(_("La réparation %s est en cours de modification par un autre utilisateur.") % rec.name)

        self.invalidate_recordset(['state', 'delivery_state'])

        # Batch validation
        errors = []
        for rec in self:
            if rec.state != 'done':
                errors.append(_("La réparation %s doit être 'Terminée' avant d'être livrée.") % rec.name)
            elif rec.delivery_state != 'none':
                errors.append(_("La réparation %s est déjà sortie de l'atelier.") % rec.name)

        if errors:
            raise UserError("\n".join(errors))

        # Batch write
        self.write({
            'delivery_state': 'delivered',
            'end_date': fields.Datetime.now()
        })

        # Cleanup activities
        pickup_type_id = self.env.ref('repair_custom.mail_act_repair_done').id
        for rec in self:
            activities = rec.activity_ids.filtered(lambda a: a.activity_type_id.id == pickup_type_id)
            if activities:
                activities.action_feedback(feedback="Client livré (Appareil récupéré)")

        return True

    def action_repair_start(self):
        self.ensure_one()
        return self.write({'state': 'under_repair'})

    def _action_repair_confirm(self):
        return self.write({'state': 'confirmed'})

    def action_validate(self):
        """Confirm repair and create device unit if needed."""
        self.ensure_one()
        if self.variant_id and self.variant_id not in self.device_id.variant_ids:
            self.device_id.write({'variant_ids': [(4, self.variant_id.id)]})
        if self.unit_id:
            return self._action_repair_confirm()
        if self.device_id and self.partner_id:
            sn = self.serial_number
            vals = {
                'device_id': self.device_id.id,
                'partner_id': self.partner_id.id,
                'serial_number': sn,
            }
            if self.variant_id:
                vals['variant_id'] = self.variant_id.id
            new_unit = self.env['repair.device.unit'].create(vals)
            self.write({'unit_id': new_unit.id, 'serial_number': new_unit.serial_number})
        return self._action_repair_confirm()

    # --- INVOICING ---
    invoice_ids = fields.One2many('account.move', 'repair_id', string="Factures générées")
    invoice_count = fields.Integer(string="Nombre de factures", compute='_compute_invoice_count')

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.invoice_ids)

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'name': "Factures",
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', self.invoice_ids.ids)],
            'context': {'default_repair_id': self.id},
        }

    # --- SALE ORDERS ---
    sale_order_id = fields.Many2one('sale.order', 'Sale Order', check_company=True, readonly=True)
    sale_order_count = fields.Integer(string="Nombre de devis/BC", compute='_compute_sale_order_count')

    @api.depends('sale_order_id')
    def _compute_sale_order_count(self):
        for rec in self:
            rec.sale_order_count = 1 if rec.sale_order_id else 0

    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return
        return {
            'name': "Devis / Bon de Commande",
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.sale_order_id.id,
            'target': 'current',
            'context': {'default_repair_id': self.id},
        }

    def action_open_pricing_wizard(self):
        self.ensure_one()
        device_categ_id = self.device_id.category_id.id if self.device_id else False
        return {
            'name': _("Facturation Atelier"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.pricing.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_repair_id': self.id,
                'default_device_categ_id': device_categ_id,
            },
        }

    # --- CREATE WITH SEQUENCE ---
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('repair.order') or 'New'
        return super(Repair, self).create(vals_list)

    # --- CONSTRAINTS ---
    @api.constrains('unit_id', 'device_id', 'variant_id', 'serial_number')
    def _check_unit_consistency(self):
        for rec in self:
            if rec.unit_id:
                if rec.device_id != rec.unit_id.device_id:
                    raise ValidationError(_("Incohérence Modèle !"))
                if rec.unit_id.variant_id and rec.variant_id != rec.unit_id.variant_id:
                    raise ValidationError(_("Incohérence Variante !"))
                if rec.unit_id.serial_number and rec.serial_number != rec.unit_id.serial_number:
                    raise ValidationError(_("Incohérence N° de série !"))

    # --- ACTIONS ---
    def action_print_repair_order(self):
        if not self.id:
            return
        self.ensure_one()
        if self.batch_id:
            return self.env.ref('repair_custom.action_report_repair_batch_ticket').report_action(self.batch_id)
        else:
            return self.env.ref('repair_custom.action_report_repair_ticket').report_action(self)

    def _assign_technician_if_needed(self):
        """Assign current technician if not already assigned."""
        if not self.technician_employee_id:
            if self.env.context.get('atelier_employee_id'):
                self.technician_employee_id = self.env.context.get('atelier_employee_id')
            elif not self.env.user.share:
                employee = self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1)
                if employee:
                    self.technician_employee_id = employee.id

    def action_atelier_start(self):
        """Workflow: Take and start repair."""
        self.ensure_one()

        # Row-level locking
        try:
            self.env.cr.execute("SELECT id FROM repair_order WHERE id=%s FOR UPDATE NOWAIT", (self.id,))
        except Exception:
            raise UserError(_("Cette réparation est en cours de modification par un autre utilisateur. Veuillez réessayer."))

        self.invalidate_recordset(['state', 'technician_employee_id'])

        if self.quote_required and self.state == 'confirmed' and not self.env.context.get('force_start'):
            return {
                'name': _("Attention : Devis Requis"),
                'type': 'ir.actions.act_window',
                'res_model': 'repair.start.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_repair_id': self.id,
                    'atelier_employee_id': self.env.context.get('atelier_employee_id')
                }
            }

        self._assign_technician_if_needed()
        vals = {'state': 'under_repair'}
        self.write(vals)

        tech_name = self.technician_employee_id.name if self.technician_employee_id else self.env.user.name
        if self.env.context.get('force_start'):
            if self.env.context.get('start_with_quote'):
                self.message_post(body=f"{tech_name} a commencé l'intervention (Devis demandé en parallèle).")
            else:
                self.message_post(body=f"⚠️ {tech_name} a forcé le démarrage (Devis ignoré).")
        else:
            self.message_post(body=f"{tech_name} a commencé l'intervention.")

        return True

    def action_atelier_request_quote(self):
        """Request quote from manager."""
        self.ensure_one()
        self._assign_technician_if_needed()

        if not self.internal_notes:
            raise UserError(_("Veuillez remplir l'estimation technique avant de demander un devis."))

        group_manager = self.env.ref('repair_custom.group_repair_manager')
        activity_type_id = self.env.ref('repair_custom.mail_act_repair_quote_validate').id

        for manager_user in group_manager.users:
            self.activity_schedule(
                activity_type_id=activity_type_id,
                user_id=manager_user.id,
                summary="Devis",
                note=f"Demande par {self.env.user.name} pour {self.device_id_name}",
                date_deadline=fields.Date.today(),
            )

        return self.write({'quote_state': 'pending'})

    def action_create_quotation_wizard(self):
        """Manager: Generate quote from alert."""
        self.ensure_one()
        device_categ_id = self.device_id.category_id.id if self.device_id else False

        return {
            'name': _("Création du Devis"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.pricing.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_repair_id': self.id,
                'default_device_categ_id': device_categ_id,
                'default_generation_type': 'quote',
            },
        }

    def action_manager_validate_quote(self):
        """Manager validates quote."""
        self.ensure_one()

        target_type_id = self.env.ref('repair_custom.mail_act_repair_quote_validate').id
        activities = self.activity_ids.filtered(lambda a: a.activity_type_id.id == target_type_id)
        if activities:
            activities.action_feedback(feedback=f"Validé par {self.env.user.name}")

        self.message_post(body="Devis validé par le management.")
        return self.write({'quote_state': 'approved'})

    def action_atelier_parts_toggle(self):
        """Toggle parts waiting status."""
        for rec in self:
            rec.parts_waiting = not rec.parts_waiting
            msg = "Pièces commandées / En attente." if rec.parts_waiting else "Pièces reçues."
            rec.message_post(body=msg)
        return True

    def action_atelier_abort(self):
        """Abort repair and return to queue."""
        self.ensure_one()

        tech_name = self.technician_employee_id.name or self.env.user.name
        self.message_post(body=f"❌ {tech_name} a abandonné l'intervention (Retour file d'attente).")

        if self.activity_ids:
            self.activity_ids.unlink()
        return self.write({
            'state': 'confirmed',
            'technician_employee_id': False,
            'quote_state': 'none',
            'parts_waiting': False,
        })

    def action_save_repair(self):
        """Explicit save for mobile."""
        self.ensure_one()
        return True

    def action_open_template_selector(self):
        """Open template selector wizard."""
        self.ensure_one()
        return {
            'name': _("Insérer un Gabarit"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.template.selector',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_repair_id': self.id,
                'default_category_id': self.category_id.id
            }
        }

    def action_merge_into_batch(self):
        """Merge selected repairs into a batch."""
        partners = self.mapped('partner_id')
        if len(partners) > 1:
            raise UserError(_("Impossible de grouper ! Les réparations sélectionnées appartiennent à des clients différents."))
        if not partners:
            return

        partner = partners[0]
        existing_batches = self.mapped('batch_id')

        if existing_batches:
            target_batch = existing_batches.sorted('id')[0]
        else:
            target_batch = self.env['repair.batch'].create({'partner_id': partner.id})

        self.write({'batch_id': target_batch.id})

        batches_to_check = existing_batches - target_batch
        for old_batch in batches_to_check:
            if not old_batch.repair_ids:
                if hasattr(old_batch, 'invoice_ids') and old_batch.invoice_ids:
                    raise UserError(_("Impossible de supprimer le dossier %s : il contient des factures.") % old_batch.name)
                if hasattr(old_batch, 'sale_order_ids') and old_batch.sale_order_ids:
                    raise UserError(_("Impossible de supprimer le dossier %s : il contient des devis.") % old_batch.name)

                old_batch.unlink()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Fusion Réussie"),
                'message': _("%s réparations ont été groupées dans le dossier %s") % (len(self), target_batch.name),
                'type': 'success',
                'sticky': False,
            }
        }
