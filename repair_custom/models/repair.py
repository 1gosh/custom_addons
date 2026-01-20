from random import randint
from datetime import date, datetime, time
from odoo import api, Command, fields, models, _
from odoo.http import request 
from odoo.exceptions import UserError, ValidationError
from dateutil.relativedelta import relativedelta
from odoo.tools import float_compare, float_is_zero, clean_context, html2plaintext
import uuid
import json


class Repair(models.Model):
    """ Repair Orders """
    _name = 'repair.order'
    _description = 'Repair Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, entry_date desc'
    _check_company_auto = True

    # --- CHAMPS STANDARDS ---
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
    
    technician_user_id = fields.Many2one('res.users', string="Technicien (Utilisateur)", readonly=True)
    
    # Champ clé pour l'assignation
    technician_employee_id = fields.Many2one('hr.employee', string="Technicien", help="Employé responsable.")
    
    user_id = fields.Many2one('res.users', string="Responsible", default=lambda self: self.env.user, check_company=True)
    tracking_token = fields.Char('Tracking Token', default=lambda self: uuid.uuid4().hex, readonly=True)
    tracking_url = fields.Char('Tracking URL', compute="_compute_tracking_url")

    @api.depends('tracking_token')
    def _compute_tracking_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for rec in self:
            rec.tracking_url = f"{base_url}/repair/tracking/{rec.tracking_token}"

    name = fields.Char('Référence', default='New', index='trigram', copy=False, required=True, readonly=True)
    company_id = fields.Many2one('res.company', 'Company', readonly=True, required=True, index=True, default=lambda self: self.env.company)
    
    state = fields.Selection([
        ('draft', 'New'),
        ('confirmed', 'Confirmed'),
        ('under_repair', 'Under Repair'),
        ('done', 'Repaired'),
        ('delivered', 'Livré / Clôturé'),
        ('cancel', 'Cancelled')], string='Status',
        copy=False, default='draft', readonly=True, tracking=True, index=True)

    quote_state = fields.Selection([
        ('none', 'Pas de devis'),
        ('draft', 'Estimation en cours'),
        ('pending', 'Attente Validation'),
        ('approved', 'Validé'),
        ('refused', 'Refusé')
    ], string="Statut Devis", default='none', tracking=True)
        
    priority = fields.Selection([('0', 'Normal'), ('1', 'Urgent')], default='0', string="Priority")
    partner_id = fields.Many2one('res.partner', 'Customer', index=True, check_company=True, required=True)
    
    # --- LOGIQUE DEVIS ---
    quote_required = fields.Boolean(string="Devis Exigé", default=False, tracking=True)
    quote_threshold = fields.Integer(string="Seuil du devis")
    quotation_notes = fields.Text(string="Estimation Technique", help="Notes pour le devis (saisies par l'atelier)")
    
    parts_waiting = fields.Boolean(string="Attente de pièces", default=False, tracking=True)
    diagnostic_notes = fields.Text(string="Diagnostic Technique")

    # ==========================================================================
    # OPTIMISATION : GESTION CENTRALISÉE DE L'HISTORIQUE ET GARANTIE
    # ==========================================================================

    # 1. CHAMPS DE DONNÉES (Calculés en base)
    has_history = fields.Boolean(compute='_compute_history_data', string="A un historique", store=False)
    
    history_repair_ids = fields.Many2many(
        'repair.order', 
        compute='_compute_history_data', 
        string="Historique Appareil"
    )

    previous_repair_id = fields.Many2one(
        'repair.order', 
        string="Dernière Réparation", 
        compute='_compute_history_data',
        store=True 
    )

    # 2. CHAMPS D'INTERFACE (Modifiables)
    repair_warranty = fields.Selection([
        ('aucune', 'Aucune'), 
        ('sav', 'SAV'), 
        ('sar', 'SAR')], 
        string="Garantie",
        default='aucune',
        copy=False
    )

    # 3. LE CERVEAU BACKEND (1 seule requête DB pour tout le monde)
    @api.depends('unit_id')
    def _compute_history_data(self):
        for rec in self:
            if not rec.unit_id:
                rec.history_repair_ids = False
                rec.has_history = False
                rec.previous_repair_id = False
                continue

            # Une seule recherche optimisée
            domain = [
                ('unit_id', '=', rec.unit_id.id),
                ('state', 'in', ['delivered', 'done']) # On cherche l'historique validé
            ]
            if isinstance(rec.id, int):
                domain.append(('id', '!=', rec.id))
            
            # On récupère tout, trié par date (le plus récent en premier)
            repairs = self.env['repair.order'].search(domain, order='end_date desc, write_date desc')
            
            rec.history_repair_ids = repairs
            rec.has_history = bool(repairs)
            rec.previous_repair_id = repairs[0] if repairs else False

    # 4. LE CERVEAU INTERFACE (1 seul Onchange pour la logique métier + Popup)
    @api.onchange('unit_id', 'entry_date')
    def _onchange_unit_workflow(self):
        """ 
        Gère à la fois :
        1. La suggestion de garantie (SAR/Aucune) sans écraser le SAV manuel.
        2. L'affichage de la Popup d'avertissement.
        """
        if not self.unit_id:
            return

        # A. On s'assure d'avoir les données fraîches (Historique)
        self._compute_history_data()
        
        # B. Logique de calcul de la garantie (SAR ?)
        is_sar = False
        prev_repair = self.previous_repair_id # Déjà calculé juste au-dessus
        
        if prev_repair:
            ref_date = prev_repair.end_date or prev_repair.write_date
            if ref_date:
                limit_date = ref_date.date() + relativedelta(months=3)
                current_date = self.entry_date.date() if self.entry_date else fields.Date.today()
                if current_date <= limit_date:
                    is_sar = True

        # C. Application de la garantie (si pas SAV manuel ou changement d'appareil)
        unit_changed = (self.unit_id != self._origin.unit_id)
        if unit_changed or self.repair_warranty != 'sav':
            self.repair_warranty = 'sar' if is_sar else 'aucune'

        # D. Gestion de la Popup (Uniquement si on change d'appareil)
        # On ne veut pas afficher la popup si on change juste la date
        if unit_changed and prev_repair:
            tech_name = prev_repair.technician_employee_id.name or 'Inconnu'
            prev_date_str = (prev_repair.end_date or prev_repair.write_date).strftime('%d/%m/%Y')
            
            # Message dynamique selon si c'est SAR ou pas
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
    
    # On récupère les infos via le lien previous_repair_id
    # store=True permet de figer la valeur et facilite la recherche
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

    # --- APPAREILS ---
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
    unit_id = fields.Many2one('repair.device.unit', string="Appareils existants", readonly=True)
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


    batch_id = fields.Many2one('repair.batch', string="Dossier de Dépôt", readonly=True)
    batch_count = fields.Integer(compute='_compute_batch_count', string="Autres appareils")

    @api.depends('batch_id')
    def _compute_batch_count(self):
        for rec in self:
            if rec.batch_id:
                domain = [('batch_id', '=', rec.batch_id.id)]

                # if isinstance(rec.id, int):
                #     domain.append(('id', '!=', rec.id))
                # # -----------------------------
                
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
        
    def write(self, vals):
        if vals.get('state') == 'draft':
            vals = dict(vals)
            vals.update({'technician_user_id': False, 'technician_employee_id': False})
        return super(Repair, self).write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_confirmed(self):
        repairs_to_cancel = self.filtered(lambda ro: ro.state not in ('draft', 'cancel'))
        repairs_to_cancel.action_repair_cancel()

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
        # 1. LE GARDE-FOU (Avec l'exception force_stop pour le Wizard)
        if self.quote_required and self.quote_state != 'approved' and not self.env.context.get('force_stop'):
            return {
                'name': _("Alerte : Devis non validé"),
                'type': 'ir.actions.act_window',
                'res_model': 'repair.warn.quote.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_repair_id': self.id}
            }
            
        # 2. CHANGEMENT D'ÉTAT
        res = self.write({
            'state': 'done', 
            'parts_waiting': False,
            'end_date': fields.Datetime.now()
        })

        # --- NETTOYAGE DES ACTIVITÉS EN COURS ---
        # On ferme proprement les demandes de devis qui traînent
        quote_act_type = self.env.ref('repair_custom.mail_act_repair_quote_validate', raise_if_not_found=False)
        
        if quote_act_type:
            # On filtre les activités liées à ces réparations (self) qui sont du type "Devis"
            activities_to_clean = self.activity_ids.filtered(lambda a: a.activity_type_id.id == quote_act_type.id)
            
            # On les valide automatiquement
            if activities_to_clean:
                activities_to_clean.action_feedback(feedback="Clôture automatique : Réparation terminée.")

        # 3. NOTIFICATION AUX MANAGERS (Logique modifiée)
        # Assurez-vous que l'ID XML ci-dessous existe bien dans votre fichier data
        pickup_type = self.env.ref('repair_custom.mail_act_repair_done', raise_if_not_found=True)
        group_manager = self.env.ref('repair_custom.group_repair_manager', raise_if_not_found=True)

        if pickup_type and group_manager:
            for rec in self:
                # On boucle sur TOUS les utilisateurs du groupe Manager
                for manager_user in group_manager.users:
                    rec.activity_schedule(
                        activity_type_id=pickup_type.id,
                        user_id=manager_user.id,
                        summary="Appareil Prêt",
                        note=f"L'appareil {rec.device_id_name} est réparé. À facturer et livrer.",
                        date_deadline=fields.Date.today(),
                    )
            
        return res

    def action_repair_end(self):
        if self.filtered(lambda repair: repair.state != 'under_repair'):
            raise UserError(_("La réparation doit être en cours pour être terminée."))
        return self.action_repair_done() 

    def action_repair_delivered(self):
        """ 
        Passage à l'état Livré.
        Compatible avec la sélection multiple en vue liste.
        """
        # 1. Vérification de sécurité (On vérifie tout AVANT d'écrire quoi que ce soit)
        for rec in self:
            if rec.state != 'done':
                raise UserError(_("La réparation %s doit être 'Terminée' avant d'être livrée.") % rec.name)
        
        # 2. Écriture en masse (Plus rapide pour la base de données)
        # On peut écrire sur 'self' directement, cela mettra à jour tous les enregistrements sélectionnés
        self.write({
            'state': 'delivered',
            'end_date': fields.Datetime.now() 
        })

        # 2. Fermeture propre de l'activité "Appeler Client"
        pickup_type_id = self.env.ref('repair_custom.mail_act_repair_done').id
        
        # On peut le faire sur le recordset 'self' entier
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

    # --- FACTURATION DIRECTE ---
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

    # Sale Order Binding
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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('repair.order') or 'New'
        return super(Repair, self).create(vals_list)

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
    
    def action_print_repair_order(self):
        if not self.id: return 
        self.ensure_one()
        if self.batch_id:
            return self.env.ref('repair_custom.action_report_repair_batch_ticket').report_action(self.batch_id)
        else:
            return self.env.ref('repair_custom.action_report_repair_ticket').report_action(self)

    # -------------------------------------------------------------------------
    # ACTIONS MÉTIER ATELIER & MANAGER (LOGIQUE FLASH vs EXPERT)
    # -------------------------------------------------------------------------

    def _assign_technician_if_needed(self):
        """ Méthode utilitaire : Assigne le technicien actuel s'il n'y en a pas déjà un """
        if not self.technician_employee_id:
            # 1. Via le contexte Kiosque (Prioritaire)
            if self.env.context.get('atelier_employee_id'):
                self.technician_employee_id = self.env.context.get('atelier_employee_id')
            # 2. Sinon via l'utilisateur connecté (si pas portail)
            elif not self.env.user.share:
                employee = self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1)
                if employee:
                    self.technician_employee_id = employee.id

    def action_atelier_start(self):
        """ 
        WORKFLOW FLASH : "Prendre & Réparer"
        - Assigne le technicien (s'il ne l'est pas déjà)
        - Passe en 'under_repair'
        - Bloque si devis exigé (sauf si force_start)
        """
        self.ensure_one()

        if self.quote_required and self.state == 'confirmed' and not self.env.context.get('force_start'):
            # Redirection vers le wizard d'avertissement au lieu de l'erreur
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
        
        # 1. Assignation
        self._assign_technician_if_needed()
        
        # 2. Changement état
        vals = {'state': 'under_repair'}
        self.write(vals)

        # 3. Log
        tech_name = self.technician_employee_id.name if self.technician_employee_id else self.env.user.name
        if self.env.context.get('force_start'):
            if self.env.context.get('start_with_quote'):
                # Cas : Bouton "Faire le devis" du Wizard
                self.message_post(body=f"{tech_name} a commencé l'intervention (Devis demandé en parallèle).")
            else:
                # Cas : Bouton "Passer" du Wizard
                self.message_post(body=f"⚠️ {tech_name} a forcé le démarrage (Devis ignoré).")
        else:
            # Cas : Démarrage standard (Pas de blocage)
            self.message_post(body=f"{tech_name} a commencé l'intervention.")

        return True
    
    def action_atelier_request_quote(self):
        self.ensure_one()

        self._assign_technician_if_needed()

        if not self.quotation_notes and self.internal_notes:
            self.quotation_notes = self.internal_notes
        if not self.quotation_notes:
            raise UserError(_("Veuillez remplir l'estimation technique avant de demander un devis."))
        
        group_manager = self.env.ref('repair_custom.group_repair_manager')
        activity_type_id = self.env.ref('repair_custom.mail_act_repair_quote_validate').id
        
        for manager_user in group_manager.users:
            self.activity_schedule(
                activity_type_id=activity_type_id, # <--- ICI
                user_id=manager_user.id,
                summary="Devis", 
                note=f"Demande par {self.env.user.name} pour {self.device_id_name}",
                date_deadline=fields.Date.today(),
            )
        
        return self.write({'quote_state': 'pending', 'quote_required': True})

    def action_create_quotation_wizard(self):
        """ MANAGER : Générer le Devis Odoo depuis l'alerte """
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
                # C'est ici qu'on force le mode Devis pour le wizard Pricing
                'default_generation_type': 'quote', 
            },
        }

    def action_manager_validate_quote(self):
        """ Le manager valide -> On nettoie les notifications et on autorise la reprise """
        self.ensure_one()

        target_type_id = self.env.ref('repair_custom.mail_act_repair_quote_validate').id
        
        # On filtre les activités de CETTE réparation qui ont CE type
        activities = self.activity_ids.filtered(lambda a: a.activity_type_id.id == target_type_id)
        if activities:
            activities.action_feedback(feedback=f"Validé par {self.env.user.name}")

        self.message_post(body="Devis validé par le management.")
        return self.write({'quote_state': 'approved'})

    def action_atelier_parts_toggle(self):
        for rec in self:
            rec.parts_waiting = not rec.parts_waiting
            msg = "Pièces commandées / En attente." if rec.parts_waiting else "Pièces reçues."
            rec.message_post(body=msg)
        return True

    def action_atelier_abort(self):
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
        """ Sauvegarde explicite pour le mobile """
        self.ensure_one()
        return True

    def action_open_template_selector(self):
        self.ensure_one()
        return {
            'name': _("Insérer un Gabarit"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.template.selector',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_repair_id': self.id,
                # On peut filtrer les gabarits par catégorie automatiquement
                'default_category_id': self.category_id.id 
            }
        }
    
    def action_merge_into_batch(self):
        """ 
        Action serveur pour grouper/fusionner la sélection dans un dossier.
        Logique :
        1. Vérifier unicité du client.
        2. Identifier s'il existe déjà des batches dans la sélection.
           - Si oui : On prend le plus vieux comme "Maître".
           - Si non : On en crée un nouveau.
        3. Déplacer toutes les réparations dans ce dossier.
        4. Supprimer les anciens dossiers s'ils sont devenus vides.
        """
        # 1. Vérification Client
        partners = self.mapped('partner_id')
        if len(partners) > 1:
            raise UserError(_("Impossible de grouper ! Les réparations sélectionnées appartiennent à des clients différents."))
        if not partners:
            return

        partner = partners[0]
        
        # 2. Identification du Dossier Cible
        existing_batches = self.mapped('batch_id')
        
        if existing_batches:
            # On prend le dossier le plus ancien (le plus petit ID) comme "Maître"
            target_batch = existing_batches.sorted('id')[0]
        else:
            # Aucun dossier, on en crée un
            target_batch = self.env['repair.batch'].create({
                'partner_id': partner.id
            })

        # 3. Déplacement / Assignation
        # On écrit sur toutes les fiches d'un coup
        self.write({'batch_id': target_batch.id})
        
        # 4. Nettoyage des dossiers devenus vides
        # On regarde les dossiers qui étaient liés avant mais qui ne sont PAS le nouveau maître
        batches_to_check = existing_batches - target_batch
        for old_batch in batches_to_check:
            # Si le dossier n'a plus aucune réparation liée, on le supprime
            if not old_batch.repair_ids:
                old_batch.unlink()

        # Notification de succès
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


# --- WIZARDS & AUTRES CLASSES ---

class RepairWarnQuoteWizard(models.TransientModel):
    _name = 'repair.warn.quote.wizard'
    _description = "Avertissement Devis"

    repair_id = fields.Many2one('repair.order')
    
    def action_force_terminate(self):
        self.ensure_one()
        
        # Log de tracabilité
        self.repair_id.message_post(body="⚠️ Clôture forcée (Devis non validé ignoré).")
        
        # MAGIE : On rappelle la méthode d'origine avec le contexte 'force_stop'
        # Cela va passer outre le 'if' bloquant et exécuter la création d'activité !
        return self.repair_id.with_context(force_stop=True).action_repair_done()

    def action_go_to_quote(self):
        self.ensure_one()
        return self.repair_id.action_atelier_request_quote()

class RepairStartWizard(models.TransientModel):
    _name = 'repair.start.wizard'
    _description = "Avertissement démarrage réparation"
    repair_id = fields.Many2one('repair.order', required=True)
    message = fields.Text(readonly=True, default="Un devis est exigé pour cette réparation. Vous pouvez faire la demande maintenant ou passer.")

    def action_force_start(self):
        self.ensure_one()
        return self.repair_id.with_context(force_start=True).action_atelier_start()

    def action_go_to_quote(self):
        """ Option 2 : On commence ET on demande le devis tout de suite """
        self.ensure_one()
        self.repair_id.with_context(force_start=True, start_with_quote=True).action_atelier_start()
        return self.repair_id.action_atelier_request_quote()
        

class RepairPickupLocation(models.Model):
    _name = 'repair.pickup.location'
    _description = 'Repair Pickup Location'
    name = fields.Char(string="Nom du lieu", required=True)
    street = fields.Char(string="Rue")
    street2 = fields.Char(string="Rue (complément)")
    city = fields.Char(string="Ville")
    zip = fields.Char(string="Code postal")
    country_id = fields.Many2one('res.country', string="Pays")
    contact_id = fields.Many2one('res.partner', string="Contact associé")
    company_id = fields.Many2one('res.company', string="Société", default=lambda self: self.env.company)
    def _compute_display_name(self):
        for location in self:
            location.display_name = f"{location.name} – {location.city}" if location.city else location.name

class RepairTags(models.Model):
    _name = "repair.tags"
    _description = "Repair Tags"
    def _get_default_color(self): return randint(1, 11)
    name = fields.Char('Nom de la panne', required=True)
    color = fields.Integer(string='Color Index', default=_get_default_color)
    is_global = fields.Boolean(string="Global", default=False)
    category_ids = fields.Many2many('repair.device.category', string="Catégories spécifiques")
    _sql_constraints = [('name_uniq', 'unique (name)', "Ce nom de panne existe déjà !")]
    
    @api.onchange('is_global')
    def _onchange_is_global_clear_categories(self):
        if self.is_global: self.category_ids = False

    @api.model
    def name_create(self, name):
        clean_name = name.strip()
        existing_tag = self.search([('name', '=ilike', clean_name)], limit=1)
        if existing_tag:
            if not existing_tag.is_global:
                cats = self.env.context.get('default_category_ids') or [self.env.context.get('default_category_id')]
                if cats and cats[0]:
                    existing_tag.write({'category_ids': [(4, c) for c in cats]})
            return existing_tag.id, existing_tag.display_name
        return super(RepairTags, self).name_create(clean_name)

class RepairDeviceUnit(models.Model):
    _inherit = 'repair.device.unit'
    repair_order_ids = fields.One2many('repair.order', 'unit_id', string="Réparations associées")
    repair_order_count = fields.Integer(string="Réparations", compute='_compute_repair_order_count')
    def _compute_repair_order_count(self):
        for rec in self:
            rec.repair_order_count = self.env['repair.order'].search_count([('unit_id', '=', rec.id)])
    def action_view_repairs(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Réparations', 'res_model': 'repair.order', 'view_mode': 'tree,form', 'domain': [('unit_id', '=', self.id)], 'context': {'default_unit_id': self.id}}

    functional_state = fields.Selection([
        ('broken', 'En panne'),
        ('fixing', 'En Atelier'),
        ('working', 'Réparé')
    ], string="État physique", compute='_compute_functional_state', store=True)

    @api.depends('repair_order_ids.state')
    def _compute_functional_state(self):
        for unit in self:
            # 1. Y a-t-il une réparation active en ce moment ?
            active_repairs = unit.repair_order_ids.filtered(
                lambda r: r.state in ['confirmed', 'under_repair']
            )
            
            if active_repairs:
                unit.functional_state = 'fixing'
            else:
                # 2. Sinon, quel est le résultat de la TOUTE DERNIÈRE intervention ?
                # On trie par date de fin (ou d'écriture) décroissante
                last_repair = unit.repair_order_ids.sorted(
                    key=lambda r: r.end_date or r.write_date, 
                    reverse=True
                )
                
                if last_repair and last_repair[0].state in ['done', 'delivered']:
                    unit.functional_state = 'working'
                else:
                    # Pas d'historique ou dernière réparation annulée/brouillon
                    unit.functional_state = 'broken'

class AccountMove(models.Model):
    _inherit = 'account.move'

    repair_id = fields.Many2one('repair.order', string="Réparation d'origine", readonly=True)
    repair_notes = fields.Text(related='repair_id.internal_notes', string="Notes de l'atelier", readonly=True)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    repair_order_ids = fields.One2many(
        comodel_name='repair.order', inverse_name='sale_order_id',
        string='Repair Order', groups='stock.group_stock_user')
    repair_count = fields.Integer(
        "Repair Order(s)", compute='_compute_repair_count', groups='stock.group_stock_user')

    @api.depends('repair_order_ids')
    def _compute_repair_count(self):
        for order in self:
            order.repair_count = len(order.repair_order_ids)
    
    def action_show_repair(self):
        self.ensure_one()
        if self.repair_count == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "repair.order",
                "views": [[False, "form"]],
                "res_id": self.repair_order_ids.id,
            }
        elif self.repair_count > 1:
            return {
                "name": _("Repair Orders"),
                "type": "ir.actions.act_window",
                "res_model": "repair.order",
                "view_mode": "tree,form",
                "domain": [('sale_order_id', '=', self.id)],
            }

class RepairNotesTemplate(models.Model):
    _name = 'repair.notes.template'
    _description = 'Gabarit de Notes'
    _order = 'name'
    name = fields.Char("Nom du Gabarit", required=True)
    template_content = fields.Text("Contenu du Gabarit")
    category_ids = fields.Many2many('repair.device.category', string="Catégories d'appareils")

class RepairTemplateSelector(models.TransientModel):
    _name = 'repair.template.selector'
    _description = "Assistant d'import de gabarit"

    repair_id = fields.Many2one('repair.order', required=True)
    category_id = fields.Many2one('repair.device.category', string="Catégorie Filtre")
    
    # On choisit le gabarit ici
    template_id = fields.Many2one('repair.notes.template', string="Choisir un modèle")
    
    # La liste des lignes à cocher/décocher
    line_ids = fields.One2many('repair.template.line', 'wizard_id', string="Lignes du gabarit")
    
    # Options
    mode = fields.Selection([
        ('add', 'Ajouter à la suite'),
        ('replace', 'Remplacer tout')
    ], string="Mode d'insertion", default='add', required=True)

    @api.onchange('template_id')
    def _onchange_template_id(self):
        """ Quand on change de gabarit, on remplit la liste des lignes """
        if not self.template_id or not self.template_id.template_content:
            self.line_ids = [(5, 0, 0)] # Vider la liste
            return

        lines = []
        # On découpe le texte par saut de ligne
        raw_lines = self.template_id.template_content.split('\n')
        
        for content in raw_lines:
            # On ignore les lignes vides pour ne pas polluer
            if content.strip():
                lines.append((0, 0, {
                    'is_selected': True, # Coché par défaut
                    'content': content.strip()
                }))
        
        self.line_ids = [(5, 0, 0)] + lines

    def action_confirm(self):
        self.ensure_one()
        
        # 1. On récupère uniquement les lignes cochées
        selected_lines = self.line_ids.filtered(lambda l: l.is_selected).mapped('content')
        
        if not selected_lines:
            return {'type': 'ir.actions.act_window_close'}

        # 2. On reconstruit le texte final
        text_to_insert = '\n'.join(selected_lines)
        
        # 3. On met à jour la réparation
        current_notes = self.repair_id.internal_notes or ""
        
        if self.mode == 'replace':
            final_text = text_to_insert
        else:
            # Si ajout, on gère proprement les sauts de ligne
            separator = "\n\n" if current_notes else ""
            final_text = f"{current_notes}{separator}{text_to_insert}"
            
        self.repair_id.internal_notes = final_text
        
        return {'type': 'ir.actions.act_window_close'}

class RepairTemplateLine(models.TransientModel):
    _name = 'repair.template.line'
    _description = "Ligne de gabarit"

    wizard_id = fields.Many2one('repair.template.selector')
    is_selected = fields.Boolean(string="Inclure", default=True)
    content = fields.Char(string="Texte")

class RepairBatch(models.Model):
    _name = 'repair.batch'
    _description = "Dossier de Dépôt"
    _order = 'date desc'
    name = fields.Char("Réf. Dossier", required=True, copy=False, readonly=True, default='New')
    date = fields.Datetime(string="Date de création", default=lambda self: fields.Datetime.now())
    repair_ids = fields.One2many('repair.order', 'batch_id', string="Réparations")
    partner_id = fields.Many2one('res.partner', string="Client")
    company_id = fields.Many2one('res.company', string="Société", default=lambda self: self.env.company)
    repair_count = fields.Integer(string="Nb Appareils", compute='_compute_repair_count', store=True)
    state = fields.Selection([('draft', 'Brouillon'), 
                            ('confirmed', 'En attente'), 
                            ('under_repair', 'En cours'), 
                            ('processed', 'Traité')], 
                            string="État", 
                            compute='_compute_state', 
                            store=True, default='draft'
    )

    @api.depends('repair_ids')
    def _compute_repair_count(self):
        for rec in self: rec.repair_count = len(rec.repair_ids)

    @api.depends('repair_ids.state')
    def _compute_state(self):
        for batch in self:
            if not batch.repair_ids:
                batch.state = 'draft'
                continue
            states = set(batch.repair_ids.mapped('state'))
            if states.issubset({'done', 'cancel'}): batch.state = 'processed'
            elif 'under_repair' in states: batch.state = 'under_repair'
            elif all(r.state == 'confirmed' for r in batch.repair_ids if r.state != 'cancel'): batch.state = 'confirmed'
            else: batch.state = 'draft'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                seq_name = self.env['ir.sequence'].next_by_code('repair.batch') or 'New'
                prefix = ""
                if vals.get('partner_id'):
                    partner = self.env['res.partner'].browse(vals['partner_id'])
                    if partner.name: prefix = f"{partner.name.upper().replace(' ', '').replace('.', '')[:4]}-"
                vals['name'] = f"{prefix}{seq_name}"
        return super(RepairBatch, self).create(vals_list)

      
class AtelierDashboardTile(models.Model):
    _name = 'atelier.dashboard.tile'
    _description = 'Tuile du Tableau de bord Atelier'
    _order = 'sequence, id' 

    sequence = fields.Integer(default=10)
    name = fields.Char("Titre", required=True)
    color = fields.Integer("Couleur")
    category_type = fields.Selection([
        ('todo', 'À faire'),
        ('progress', 'En cours (Moi)'),
        ('waiting', 'Attente de pièces'),
        ('quote_waiting', 'Devis en attente'),
        ('quote_validated', 'Devis validé'),
        ('today', 'Activité du jour'),
        ('done', 'Terminées'),
    ], string="Type de catégorie", required=True)
    
    count_reparations = fields.Integer(compute='_compute_count', string="Nombre")

    def _compute_count(self):
        Reparation = self.env['repair.order']
        # On récupère l'ID du technicien "Pierre" transmis par le login
        employee_id = self._context.get('atelier_employee_id')
        current_uid = self.env.uid
        
        for record in self:
            domain = []
            
            # --- 1. Filtre À FAIRE ---
            if record.category_type == 'todo':
                domain = [('state', '=', 'confirmed')]
                
            # --- 2. Filtre EN COURS (Logique Kiosque) ---
            elif record.category_type == 'progress':
                domain = [
                    ('state', '=', 'under_repair'),
                    ('quote_state', '!=', 'pending')
                ]
                # Si on est en mode Kiosque (Pierre est là), on compte SES réparations
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                # Sinon (Admin classique), on compte celles de son user
                else:
                    domain.append(('user_id', '=', self.env.uid))

            # --- 3. Autres filtres ---
            elif record.category_type == 'waiting':
                domain = [('parts_waiting', '=', True)]
                # AJOUT DU FILTRE PROPRIÉTAIRE
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', current_uid))
            elif record.category_type == 'quote_waiting':
                domain = [
                    ('state', '=', 'under_repair'), 
                    ('quote_state', '=', 'pending')
                ]
                # AJOUT DU FILTRE PROPRIÉTAIRE
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', current_uid))
            elif record.category_type == 'quote_validated':
                domain = [
                    ('state', '=', 'under_repair'), 
                    ('quote_state', '=', 'approved')
                ]
                # AJOUT DU FILTRE PROPRIÉTAIRE
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', current_uid))
            elif record.category_type == 'today':
                today_start = datetime.combine(date.today(), time.min)
                # Réparations modifiées aujourd'hui PAR le technicien
                domain = [('write_date', '>=', today_start)]
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', self.env.uid))
            elif record.category_type == 'done':
                domain = [('state', '=', 'done')]
                # Filtre Propriétaire
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', current_uid))
            
            # Sécurité globale sur les compteurs (pas d'annulés)
            domain.append(('state', '!=', 'cancel'))
            
            # Pour les tuiles de travail (todo/waiting), on ne veut pas les brouillons accidentels
            if record.category_type in ['todo', 'waiting']:
                 domain.append(('state', '!=', 'draft'))

            record.count_reparations = Reparation.search_count(domain)

    def action_open_reparations(self):
        self.ensure_one()
        
        action = self.env['ir.actions.act_window']._for_xml_id('repair_custom.action_repair_order_atelier')
        
        today_start = datetime.combine(date.today(), time.min)
        ctx = self._context.copy()
        
        # On initialise le domaine de base (on écrase celui du XML pour être sûr)
        domain = [('state', 'not in', ['draft', 'cancel'])]
        if self.category_type == 'todo':
            ctx.update({'search_default_todo': 1})
            domain.append(('state', '!=', 'draft'))

        elif self.category_type == 'progress':
            ctx.update({'search_default_in_progress': 1, 'search_default_my_session': 1})
            if ctx.get('atelier_employee_id'):
                ctx.update({'default_technician_employee_id': ctx.get('atelier_employee_id')})

        elif self.category_type == 'waiting':
            ctx.update({'search_default_parts': 1, 'search_default_my_session': 1})

        elif self.category_type == 'quote_waiting':
            ctx.update({'search_default_quote_waiting': 1, 'search_default_my_session': 1})

        elif self.category_type == 'quote_validated':
            ctx.update({'search_default_quote_validated': 1, 'search_default_my_session': 1})
            
        elif self.category_type == 'done':
            ctx.update({'search_default_done': 1, 'search_default_my_session': 1})

        elif self.category_type == 'today':
            # Cas spécial : Historique
            domain.append(('write_date', '>=', today_start))
            ctx.update({'search_default_my_session': 1})
            
            # Pour l'historique, on veut peut-être forcer une autre vue Tree
            # Mais on garde la logique de l'action XML pour le reste
            history_view = self.env.ref('repair_custom.view_repair_order_atelier_history_tree', raise_if_not_found=False)
            if history_view:
                
                action['views'] = [
                    (history_view.id, 'tree'),
                    (self.env.ref('repair_custom.view_repair_order_atelier_kanban').id, 'kanban'),
                    (self.env.ref('repair_custom.view_repair_order_atelier_form').id, 'form'),
                ]

        action['domain'] = domain
        action['context'] = ctx
        action['name'] = self.name

        return action

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def action_login_atelier(self):
        self.ensure_one()
        
        # On cible la vue Kanban des TUILES (pas des réparations)
        # Assurez-vous que l'ID xml 'view_atelier_dashboard_kanban' existe bien dans votre XML
        dashboard_view = self.env.ref('repair_custom.view_atelier_dashboard_kanban', raise_if_not_found=False)
        
        return {
            'name': _("Tableau de bord - %s") % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'atelier.dashboard.tile', 
            'view_mode': 'kanban',
            'view_id': dashboard_view.id if dashboard_view else False,
            'target': 'main',
            'context': {
                # C'est la seule chose qui compte ici : transmettre l'identité
                'atelier_employee_id': self.id, 
                'create': False, # Pas de bouton "Créer" sur le dashboard
            }
        }