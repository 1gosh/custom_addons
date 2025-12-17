from random import randint
from datetime import date, datetime, time
import uuid
from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero, clean_context
from odoo.tools.misc import format_date, groupby


class Repair(models.Model):
    """ Repair Orders """
    _name = 'repair.order'
    _description = 'Repair Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, entry_date desc'
    _check_company_auto = True

    entry_date = fields.Datetime(
        string="Date d'entrée",
        default=lambda self: fields.Datetime.now(),
        help="Date et heure d'entrée de l'appareil."
    )
    device_picture = fields.Image()
    last_action_time = fields.Char(
        string="Heure",
        compute='_compute_last_action_time',
        help="Heure de la dernière modification (format court)"
    )
    @api.depends('write_date')
    def _compute_last_action_time(self):
        for rec in self:
            if rec.write_date:
                # 1. On convertit la date UTC (serveur) vers le fuseau horaire de l'utilisateur
                user_tz_dt = fields.Datetime.context_timestamp(self, rec.write_date)
                # 2. On formate juste l'heure (HH:MM)
                rec.last_action_time = user_tz_dt.strftime('%H:%M')
            else:
                rec.last_action_time = ""

    @api.model
    def _default_location(self):
       return self.env['repair.pickup.location'].search([('name', '=', 'Boutique')], limit=1).id

    pickup_location_id = fields.Many2one(
        'repair.pickup.location',
        string="Lieu de prise en charge",
        help="Endroit où l'appareil a été récupéré (boutique ou atelier).",
        required=True,
        default=_default_location
    )
    import_state = fields.Char("Statut pour l'import")
    repair_warranty = fields.Selection([('aucune', 'Aucune'), ('sav', 'SAV'), ('sar', 'SAR'),], string="Garantie", default='aucune')
    notes = fields.Text(string="Notes")
    
    technician_user_id = fields.Many2one(
        'res.users',
        string="Technicien (Utilisateur)",
        readonly=True,
        help="Utilisateur Odoo ayant éffectué la réparation."
    )
    technician_employee_id = fields.Many2one(
        'hr.employee',
        string="Technicien",
        help="Employé ayant démarré la réparation."
    )
    user_id = fields.Many2one('res.users', string="Responsible", default=lambda self: self.env.user, check_company=True)
    tracking_token = fields.Char('Tracking Token', default=lambda self: uuid.uuid4().hex, readonly=True)
    tracking_url = fields.Char(
    'Tracking URL',
    compute="_compute_tracking_url"
    )

    @api.depends('tracking_token')
    def _compute_tracking_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for rec in self:
            rec.tracking_url = f"{base_url}/repair/tracking/{rec.tracking_token}"

    name = fields.Char(
        'Référence',
        default='New', index='trigram',
        copy=False, required=True,
        readonly=True)
    company_id = fields.Many2one(
        'res.company', 'Company',
        readonly=True, required=True, index=True,
        default=lambda self: self.env.company)
    state = fields.Selection([
        ('draft', 'New'),
        ('confirmed', 'Confirmed'),
        ('quotation_pending', 'Attente Devis'),
        ('quotation_approved', 'Devis Validé'),
        ('under_repair', 'Under Repair'),
        ('done', 'Repaired'),
        ('cancel', 'Cancelled')], string='Status',
        copy=False, default='draft', readonly=True, tracking=True, index=True,
        help="* The \'New\' status is used when a user is encoding a new and unconfirmed repair order.\n"
             "* The \'Confirmed\' status is used when a user confirms the repair order.\n"
             "* The \'Under Repair\' status is used when the repair is ongoing.\n"
             "* The \'Repaired\' status is set when repairing is completed.\n"
             "* The \'Cancelled\' status is used when user cancel repair order.")
    priority = fields.Selection([('0', 'Normal'), ('1', 'Urgent')], default='0', string="Priority")
    partner_id = fields.Many2one(   
        'res.partner', 'Customer',
        index=True, check_company=True, change_default=True,
        help='Choose partner for whom the order will be invoiced and delivered. You can find a partner by its Name, TIN, Email or Internal Reference.')
    quote_required = fields.Boolean(
        string="Devis Exigé",
        default=False,
        help="Si coché, le technicien devra demander un devis avant de terminer la réparation.",
        tracking=True
    )
    quotation_notes = fields.Text(
        string="Estimation Technique",
        help="Liste des pièces et temps de main d'œuvre estimés pour le devis."
    )

    parts_waiting = fields.Boolean(
        string="Attente de pièces",
        default=False,
        help="Indique que des pièces sont en commande pour cet appareil.",
        tracking=True
    )
    diagnostic_notes = fields.Text(
        string="Diagnostic Technique",
        help="Notes du technicien pour l'élaboration du devis."
    )
    # --- Appareil lié à la réparation ---
    category_id = fields.Many2one(
        'repair.device.category',
        string="Catégorie",
        ondelete="set null",
        check_company=True,
        help="Catégorie d'appareil sélectionnée en premier pour filtrer les modèles."
    )
    device_id = fields.Many2one(
        'repair.device',
        string="Modèle",
        ondelete="restrict",
        help="Modèle d'appareil (ex: Marantz 2226B)."
    )
    variant_id = fields.Many2one(
        'repair.device.variant',
        string="Variante",
        help="Variante du modèle (ex: MKII, révision, couleur, etc.)."
    )
    variant_ids_available = fields.Many2many(
        'repair.device.variant',
        compute='_compute_variant_ids_available',
        string="Variantes dispo.",
        store=False,
    )

    @api.depends('device_id', 'device_id.variant_ids')
    def _compute_variant_ids_available(self):
        for rec in self:
            rec.variant_ids_available = rec.device_id.variant_ids if rec.device_id else False

    @api.onchange('device_id')
    def _onchange_device_id_set_category(self):
        """ 
        Quand l'utilisateur choisit un appareil, on remplit la catégorie 
        automatiquement si elle n'est pas déjà définie ou différente.
        """
        # On vide la variante si on change d'appareil
        self._onchange_device_id_clear_variant()

        if self.device_id and self.device_id.category_id:
            # On assigne la catégorie de l'appareil au champ de la réparation
            self.category_id = self.device_id.category_id
            
    serial_number = fields.Char(
        "N° de série",
        store=True,
        readonly=False,
        help="Numéro de série de l'appareil lié. Si aucune unité n'est encore créée, il sera rempli lors de la confirmation."
    )
    unit_id = fields.Many2one(
        'repair.device.unit',
        string="Appareils existants",
        readonly=True,
        domain="[('device_id', '=', device_id), ('partner_id', '=', partner_id)]",
        help="Appareil physique unique correspondant au modèle/variante/numéro de série."
    )
    device_id_name = fields.Char(
        "Appareil",
        related="unit_id.device_name",
        store=True,
        readonly=False
    )

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
        # On garde uniquement la logique de nettoyage
        if self.device_id and self.category_id:
            category_of_device = self.device_id.category_id
            selected_category = self.category_id
            
            allowed_categories = selected_category + selected_category.search([('id', 'child_of', selected_category.id)])
            
            if category_of_device.id not in allowed_categories.ids:
                 self.device_id = False
                 self.variant_id = False

    tag_ids = fields.Many2many('repair.tags', string="Pannes")
    internal_notes = fields.Text("Notes de réparation") 
    notes_template_id = fields.Many2one(
        'repair.notes.template', 
        string="Insérer un Gabarit",
        store=False,
        help="Sélectionnez un gabarit pour insérer son contenu dans les notes internes."
    )
    
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
        """Remplit les champs liés quand une unité est sélectionnée"""
        for rec in self:
            if rec.unit_id:
                rec.serial_number = rec.unit_id.serial_number
                rec.device_id = rec.unit_id.device_id
                rec.variant_id = rec.unit_id.variant_id

    def action_open_unit(self):
        """Ouvre directement la fiche de l'unité (en utilisant l'action du module repair_devices)."""
        self.ensure_one()
        if not self.unit_id:
            raise UserError(_("Aucun appareil n'est associé à cette réparation."))

        # récupère l’action existante dans le module repair_devices
        action = self.env.ref('repair_devices.action_repair_device_unit').read()[0]

        # surcharge les valeurs
        action.update({
            'views': [(False, 'form')],
            'res_id': self.unit_id.id,
            'target': 'current',
        })
        return action

     # Indicateur pratique pour la vue: afficher le champ unit seulement si utile
    show_unit_field = fields.Boolean(
        string="Afficher champ unité",
        compute="_compute_show_unit_field",
    )

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

    # Sale Order Binding
    sale_order_id = fields.Many2one(
        'sale.order', 'Sale Order', check_company=True, readonly=True,
        copy=False, help="Sale Order from which the Repair Order comes from.")
    
    batch_id = fields.Many2one(
        'repair.batch', 
        string="Dossier de Dépôt", 
        readonly=True,
        copy=False
    )

    batch_count = fields.Integer(compute='_compute_batch_count', string="Autres appareils")

    @api.depends('batch_id')
    def _compute_batch_count(self):
        for rec in self:
            if rec.batch_id:
                domain = [('batch_id', '=', rec.batch_id.id)]
                
                if isinstance(rec.id, int):
                    domain.append(('id', '!=', rec.id))
                
                rec.batch_count = self.env['repair.order'].search_count(domain)
            else:
                rec.batch_count = 0


    def action_add_device_to_batch(self):
        self.ensure_one()
        
        if not self.batch_id:
            new_batch = self.env['repair.batch'].create({
                'partner_id': self.partner_id.id
            })
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

    def action_view_batch_repairs(self):
        self.ensure_one()
        if not self.batch_id:
            return
            
        return {
            'name': _("Dossier %s") % self.batch_id.name,
            'type': 'ir.actions.act_window',
            'res_model': 'repair.order',
            'view_mode': 'tree,form',
            'domain': [('batch_id', '=', self.batch_id.id)], # On affiche tout le dossier
            'context': {'create': False},
        }
        
    def write(self, vals):
        # When going back to draft, clear technician links
        if vals.get('state') == 'draft':
            vals = dict(vals)  # copy to avoid mutating caller's dict
            vals.update({
                'technician_user_id': False,
                'technician_employee_id': False,
            })
        return super(Repair, self).write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_confirmed(self):
        repairs_to_cancel = self.filtered(lambda ro: ro.state not in ('draft', 'cancel'))
        repairs_to_cancel.action_repair_cancel()

    def action_repair_cancel(self):
        admin = self.env.user.has_group('repair_custom.group_repair_admin')

        if not admin and any(repair.state == 'done' for repair in self):
            raise UserError(_("You cannot cancel a Repair Order that's already been completed"))
        
        return self.write({'state': 'cancel'})

    def action_repair_cancel_draft(self):
        if self.filtered(lambda repair: repair.state != 'cancel'):
            self.action_repair_cancel()
        return self.write({'state': 'draft'})

    def action_repair_done(self):
        return self.write({'state': 'done'})

    def action_repair_end(self):
        if self.filtered(lambda repair: repair.state != 'under_repair'):
            raise UserError(_("Repair must be under repair in order to end reparation."))

        return self.action_repair_done() 

    def action_repair_start(self):
        self.ensure_one()
        return {
            'name': _("Qui prend en charge cette réparation ?"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.technician.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_repair_id': self.id}
        }

        for repair in self:
            repair.message_post(
            body=_(
                "<b>%s</b> a démarré la réparation le %s."
            ) % (employee.name if employee else user.name, fields.Datetime.now().strftime('%d/%m/%Y à %H:%M')),
            message_type="comment",
            subtype_xmlid="mail.mt_note",  # empêche l'envoi d'email
        )

        return res  

    def _action_repair_confirm(self):
        """ Repair order state is set to 'Confirmed'.
        @param *arg: Arguments
        @return: True
        """
        # repairs_to_confirm = self.filtered(lambda repair: repair.state == 'draft')
        # repairs_to_confirm._check_company()
        # repairs_to_confirm.write({'state': 'confirmed'})
        return self.write({'state': 'confirmed'})  

    def action_validate(self):
        self.ensure_one()

        if self.variant_id and self.variant_id not in self.device_id.variant_ids:
            self.device_id.write({'variant_ids': [(4, self.variant_id.id)]})
        if self.unit_id:
            return self._action_repair_confirm()
        if self.device_id and self.partner_id:
            sn = self.serial_number or f"{uuid.uuid4().hex[:8].upper()}"
            vals = {
                'device_id': self.device_id.id,
                'partner_id': self.partner_id.id,
                'serial_number': sn,
            }
            if self.variant_id:
                vals['variant_id'] = self.variant_id.id

            new_unit = self.env['repair.device.unit'].create(vals)
            self.write({
                'unit_id': new_unit.id,
                'serial_number': new_unit.serial_number
            })
        return self._action_repair_confirm()

    # --- AJOUTS POUR LA FACTURATION DIRECTE ---

    invoice_ids = fields.One2many(
        'account.move', 
        'repair_id', 
        string="Factures générées"
    )
    
    invoice_count = fields.Integer(
        string="Nombre de factures", 
        compute='_compute_invoice_count'
    )

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.invoice_ids)
    
    def action_view_invoices(self):
        """ Bouton intelligent pour voir les factures liées """
        self.ensure_one()
        return {
            'name': "Factures",
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', self.invoice_ids.ids)],
            'context': {'default_repair_id': self.id},
        }

    sale_order_count = fields.Integer(
        string="Nombre de devis/BC",
        compute='_compute_sale_order_count'
    )

    @api.depends('sale_order_id')
    def _compute_sale_order_count(self):
        # Puisqu'on ne supporte qu'un seul SO par RO via sale_order_id, 
        # le compteur est soit 1, soit 0.
        for rec in self:
            rec.sale_order_count = 1 if rec.sale_order_id else 0

    def action_view_sale_order(self):
        """ Bouton intelligent pour voir le devis/BC lié """
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
        """ Ouvre le wizard de tarification custom """
        self.ensure_one()
        
        # CORRECTION ICI : On passe par device_id pour trouver la catégorie
        device_categ_id = False
        if self.device_id and self.device_id.category_id:
            device_categ_id = self.device_id.category_id.id
        
        return {
            'name': _("Facturation Atelier"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.pricing.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_repair_id': self.id,
                # On passe l'ID de votre catégorie custom (repair.device.category)
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
                # 1. Vérifier le Modèle
                if rec.device_id != rec.unit_id.device_id:
                    raise ValidationError(_(
                        "Incohérence ! Le modèle sélectionné (%s) ne correspond pas "
                        "à celui de l'unité liée (%s). Veuillez détacher l'unité si vous changez de modèle."
                    ) % (rec.device_id.name, rec.unit_id.device_id.name))
                
                # 2. Vérifier la Variante (si applicable)
                if rec.unit_id.variant_id and rec.variant_id != rec.unit_id.variant_id:
                    raise ValidationError(_("Incohérence sur la variante par rapport à l'unité liée."))

                # 3. Vérifier le N° Série (si applicable)
                if rec.unit_id.serial_number and rec.serial_number != rec.unit_id.serial_number:
                    raise ValidationError(_(
                        "Incohérence ! Le N° de série saisi (%s) diffère de celui de l'unité enregistrée (%s)."
                    ) % (rec.serial_number, rec.unit_id.serial_number))

    @api.model
    def _migrate_category_from_device(self):
        """
        Remplir le nouveau champ category_id pour tous les Ordres de Réparation existants
        qui ont un device_id.
        """
        # Chercher tous les Ordres de Réparation ayant un appareil défini mais sans catégorie
        repairs_to_update = self.search([
            ('device_id', '!=', False),
            ('category_id', '=', False)
        ])
        
        # Le traitement par lots (batch) est crucial pour la performance
        for repair in repairs_to_update:
            # Récupérer la catégorie à partir du modèle d'appareil
            category = repair.device_id.category_id
            
            if category:
                # Écrire la nouvelle valeur (écriture individuelle optimisée)
                repair.write({'category_id': category.id})
                
        self.env.cr.commit()
        return True
    
    def action_print_repair_order(self):
        if not self.id:
            return 
            
        self.ensure_one()
        
        if self.batch_id:
            # On passe l'ID du dossier
            return self.env.ref('repair_custom.action_report_repair_batch_ticket').report_action(self.batch_id)
        else:
            return self.env.ref('repair_custom.action_report_repair_ticket').report_action(self)

    # --- ACTIONS MÉTIER ATELIER (LOGIQUE BOUTONS) ---

    def action_atelier_start(self):
        """ 
        Démarrage intelligent :
        1. Si on vient du Kiosque, on assigne l'employé du contexte.
        2. Sinon, on assigne l'employé lié à l'utilisateur connecté.
        3. On passe en 'under_repair'.
        """
        self.ensure_one()

        if self.quote_required and self.state == 'confirmed':
            raise UserError("Un devis est exigé ! Veuillez d'abord faire la demande.")
        
        # Qui est le technicien ?
        employee_id = False
        
        # A. Via le Kiosque (Contexte)
        if self.env.context.get('atelier_employee_id'):
            employee_id = self.env.context.get('atelier_employee_id')
        
        # B. Via Login standard (Utilisateur courant)
        if not employee_id:
            employee = self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1)
            if employee:
                employee_id = employee.id

        vals = {'state': 'under_repair'}
        if employee_id:
            vals['technician_employee_id'] = employee_id
            
        # Petit message dans le fil de discussion
        if employee_id:
            tech_name = self.env['hr.employee'].browse(employee_id).name
            self.message_post(body=f"🔧 <b>{tech_name}</b> a commencé l'intervention.")

        return self.write(vals)

    def action_atelier_request_quote(self):
        """ Ouvre le Wizard pour saisir l'estimation """
        self.ensure_one()
        
        return {
            'name': _("Estimation pour Devis"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.quotation.wizard',
            'view_mode': 'form',
            'target': 'new', # Pop-up
            'context': {
                'default_repair_id': self.id
            }
        }

    def action_manager_validate_quote(self):
        """ Le manager valide -> Retour atelier """
        self.ensure_one()
        self.message_post(body="✅ Devis validé par le client/manager. Reprise de l'intervention.")
        return self.write({
            'state': 'quotation_approved'
        })

    def action_atelier_parts_toggle(self):
        """ Bascule simple du statut 'Attente Pièces' """
        for rec in self:
            rec.parts_waiting = not rec.parts_waiting
            if rec.parts_waiting:
                rec.message_post(body="📦 Pièces commandées / En attente.")
            else:
                rec.message_post(body="✅ Pièces reçues.")
        return True

    def action_atelier_finish(self):
        """ Ouvre le Wizard de clôture """
        self.ensure_one()
        return {
            'name': _("Clôture de l'intervention"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.finish.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_repair_id': self.id}
        }


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
    company_id = fields.Many2one(
        'res.company',
        string="Société",
        default=lambda self: self.env.company,
    )

    def _compute_display_name(self):
        for location in self:
            if location.city:
                location.display_name = f"{location.name} – {location.city}"
            else:
                location.display_name = location.name

class RepairTags(models.Model):
    """ Tags of Repair's tasks """
    _name = "repair.tags"
    _description = "Repair Tags"

    def _get_default_color(self):
        return randint(1, 11)

    name = fields.Char('Nom de la panne', required=True)
    color = fields.Integer(string='Color Index', default=_get_default_color)
    is_global = fields.Boolean(
        string="Global", 
        default=False,
        help="Si coché, cette panne sera proposée pour tous les types d'appareils."
    )

    category_ids = fields.Many2many(
        'repair.device.category',
        string="Catégories spécifiques",
        help="Si défini, ce tag n'apparaîtra que pour ces catégories."
    )

    _sql_constraints = [
        ('name_uniq', 'unique (name)', "Ce nom de panne existe déjà !"),
    ]

    @api.onchange('is_global')
    def _onchange_is_global_clear_categories(self):
        """ Si on passe en global, on détache les catégories spécifiques """
        if self.is_global:
            self.category_ids = False

    @api.model
    def name_create(self, name):
        """
        Logique de création / sélection intelligente :
        1. Si le tag existe et est GLOBAL -> On ne fait rien (on l'utilise tel quel).
        2. Si le tag existe et est SPÉCIFIQUE -> On lui ajoute la catégorie actuelle.
        3. Si le tag n'existe pas -> On le crée avec la catégorie actuelle.
        """
        clean_name = name.strip()
        existing_tag = self.search([('name', '=ilike', clean_name)], limit=1)

        if existing_tag:
            if existing_tag.is_global:
                return existing_tag.id, existing_tag.display_name

            cats_to_add = []
            if self.env.context.get('default_category_ids'):
                cats_to_add = self.env.context.get('default_category_ids')
            elif self.env.context.get('default_category_id'):
                 cats_to_add = [self.env.context.get('default_category_id')]
            
            if cats_to_add:
                existing_tag.write({'category_ids': [(4, c) for c in cats_to_add]})
            
            return existing_tag.id, existing_tag.display_name

        return super(RepairTags, self).name_create(clean_name)


class RepairDeviceUnit(models.Model):
    _inherit = 'repair.device.unit'

    repair_order_ids = fields.One2many(
        'repair.order',
        'unit_id',
        string="Réparations associées"
    )
    repair_order_count = fields.Integer(
        string="Réparations",
        compute='_compute_repair_order_count'
    )

    def _compute_repair_order_count(self):
        for rec in self:
            rec.repair_order_count = self.env['repair.order'].search_count([
                ('unit_id', '=', rec.id)
            ])

    def action_view_repairs(self):
        """Ouvre les ordres de réparation associés à cette unité."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Réparations associées',
            'res_model': 'repair.order',
            'view_mode': 'tree,form',
            'domain': [('unit_id', '=', self.id)],
            'context': {'default_unit_id': self.id},
        }

class AccountMove(models.Model):
    _inherit = 'account.move'

    # Lien vers la réparation
    repair_id = fields.Many2one(
        'repair.order', 
        string="Réparation d'origine",
        readonly=True,
        help="La réparation qui a généré cette facture."
    )

    repair_notes = fields.Text(
        related='repair_id.internal_notes', 
        string="Notes de l'atelier", 
        readonly=True
    )

class RepairNotesTemplate(models.Model):
    _name = 'repair.notes.template'
    _description = 'Gabarit de Notes de Réparation'
    _order = 'name'

    name = fields.Char("Nom du Gabarit", required=True)
    
    # Le contenu texte brut à insérer dans le champ internal_notes
    template_content = fields.Text("Contenu du Gabarit")
    
    # Rendre le gabarit utilisable pour certaines catégories d'appareils (Optionnel)
    category_ids = fields.Many2many(
        'repair.device.category',
        string="Catégories d'appareils"
    )

class RepairBatch(models.Model):
    _name = 'repair.batch'
    _description = "Dossier de Dépôt (Groupe)"
    
    name = fields.Char("Réf. Dossier", required=True, copy=False, readonly=True, default='New')
    date = fields.Datetime(
        string="Date de création",
        default=lambda self: fields.Datetime.now(),
        help="Date de création du dossier"
    )
    repair_ids = fields.One2many('repair.order', 'batch_id', string="Réparations")
    partner_id = fields.Many2one('res.partner', string="Client")
    company_id = fields.Many2one(
        'res.company',
        string="Société",
        default=lambda self: self.env.company,
    )
    repair_count = fields.Integer(string="Nb Appareils", compute='_compute_repair_count', store=True)

    @api.depends('repair_ids')
    def _compute_repair_count(self):
        for rec in self:
            rec.repair_count = len(rec.repair_ids)

    state = fields.Selection([
        ('draft', 'Brouillon'),        # Pas encore généré
        ('confirmed', 'En attente'),   # Tout est confirmé (prêt à être réparé)
        ('under_repair', 'En cours'),  # Au moins un appareil sur l'établi
        ('processed', 'Traité')        # Tout est fini
    ], string="État", compute='_compute_state', store=True, default='draft')

    # 2. NOUVELLE LOGIQUE DE CALCUL
    @api.depends('repair_ids.state')
    def _compute_state(self):
        for batch in self:
            if not batch.repair_ids:
                batch.state = 'draft'
                continue

            states = set(batch.repair_ids.mapped('state'))

            if states.issubset({'done', 'cancel'}):
                batch.state = 'processed'
            elif 'under_repair' in states:
                batch.state = 'under_repair'
            elif all(r.state == 'confirmed' for r in batch.repair_ids if r.state != 'cancel'):
                batch.state = 'confirmed'
            else:
                batch.state = 'draft'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                seq_name = self.env['ir.sequence'].next_by_code('repair.batch') or 'New'
                
                prefix = ""
                if vals.get('partner_id'):
                    partner = self.env['res.partner'].browse(vals['partner_id'])
                    if partner.name:
                        clean_name = partner.name.upper().replace(' ', '').replace('.', '')[:4]
                        prefix = f"{clean_name}-"
                
                # 3. Assembler le tout
                vals['name'] = f"{prefix}{seq_name}"
                
        return super(RepairBatch, self).create(vals_list)

class RepairFinishWizard(models.TransientModel):
    _name = 'repair.finish.wizard'
    _description = "Assistant de clôture"

    repair_id = fields.Many2one('repair.order', string="Réparation", required=True)
    
    template_id = fields.Many2one(
        'repair.notes.template', 
        string="Gabarit rapide",
        help="Pré-remplit le résumé avec un texte type."
    )
    
    work_summary = fields.Text(
        string="Travaux effectués", 
        required=True, 
        help="Détaillez l'intervention pour l'historique et la facturation."
    )

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id and self.template_id.template_content:
            current_text = self.work_summary or ""
            # On ajoute le gabarit à la suite ou on remplace si vide
            if current_text:
                self.work_summary = current_text + "\n" + self.template_id.template_content
            else:
                self.work_summary = self.template_id.template_content

    def action_terminate(self):
        self.ensure_one()
        
        # 1. On sauvegarde les notes
        # On garde l'historique s'il y en avait déjà
        old_notes = self.repair_id.internal_notes or ""
        separator = "\n\n--- CLÔTURE ---\n" if old_notes else ""
        
        new_notes = f"{old_notes}{separator}{self.work_summary}"
        
        # 2. On met à jour la réparation
        self.repair_id.write({
            'internal_notes': new_notes,
            'state': 'done',
            'parts_waiting': False, # On nettoie les flags
            'quote_required': False
        })
        
        # 3. Message de confirmation
        self.repair_id.message_post(body="Réparation terminée et clôturée.")
        
        return {'type': 'ir.actions.act_window_close'}


class RepairQuotationWizard(models.TransientModel):
    _name = 'repair.quotation.wizard'
    _description = "Assistant Demande de Devis"

    repair_id = fields.Many2one('repair.order', string="Réparation", required=True)
    
    # On force la saisie ici
    quotation_notes = fields.Text(
        string="Estimation Technique", 
        required=True,
        help="Détaillez les pièces et la main d'œuvre pour que le manager puisse faire le devis."
    )

    def action_confirm_request(self):
        """ Valide la demande et met à jour la réparation """
        self.ensure_one()
        
        # 1. On enregistre l'estimation dans la fiche de réparation
        # 2. On change l'état en 'quotation_pending'
        # 3. On active le drapeau 'quote_required'
        self.repair_id.write({
            'quotation_notes': self.quotation_notes,
            'state': 'quotation_pending',
            'quote_required': True
        })
        
        return {'type': 'ir.actions.act_window_close'}


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
    ], string="Type de catégorie", required=True)
    
    count_reparations = fields.Integer(compute='_compute_count', string="Nombre")

    def _compute_count(self):
        Reparation = self.env['repair.order']
        # On récupère l'ID du technicien "Pierre" transmis par le login
        employee_id = self._context.get('atelier_employee_id')
        today_start = datetime.combine(date.today(), time.min)
        
        for record in self:
            domain = []
            
            # --- 1. Filtre À FAIRE ---
            if record.category_type == 'todo':
                domain = [('state', '=', 'confirmed')]
                
            # --- 2. Filtre EN COURS (Logique Kiosque) ---
            elif record.category_type == 'progress':
                domain = [('state', '=', 'under_repair')]
                # Si on est en mode Kiosque (Pierre est là), on compte SES réparations
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                # Sinon (Admin classique), on compte celles de son user
                else:
                    domain.append(('user_id', '=', self.env.uid))

            # --- 3. Autres filtres ---
            elif record.category_type == 'waiting':
                domain = [('parts_waiting', '=', True)]
            elif record.category_type == 'quote_waiting':
                domain = [('state', 'in', ['quotation_pending'])]
            elif record.category_type == 'quote_validated':
                domain = [('state', 'in', ['quotation_approved'])]
            elif record.category_type == 'today':
                # Réparations modifiées aujourd'hui PAR le technicien
                domain = [('write_date', '>=', today_start)]
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', self.env.uid))
            
            # Sécurité globale sur les compteurs (pas d'annulés)
            domain.append(('state', '!=', 'cancel'))
            
            # Pour les tuiles de travail (todo/waiting), on ne veut pas les brouillons accidentels
            if record.category_type in ['todo', 'waiting']:
                 domain.append(('state', '!=', 'draft'))

            record.count_reparations = Reparation.search_count(domain)

    def action_open_reparations(self):
        self.ensure_one()
        
        # IMPORTANT : On garde le contexte actuel (qui contient 'atelier_employee_id')
        today_start = datetime.combine(date.today(), time.min)
        ctx = self._context.copy()
        domain = [('state', 'not in', ['draft', 'cancel'])]
        
        # On prépare l'action de base
        action = {
            'name': self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'repair.order',
            'view_mode': 'tree,form',
            'context': ctx,
            # Ceinture de sécurité (Domaine dur)
            'domain': domain, 
        }
        
        # --- Activation des filtres "Retirables" (Search Defaults) ---
        # Ces clés ('search_default_XXX') correspondent aux 'name' définis dans votre XML de recherche

        if self.category_type == 'today':
            # On applique le filtre temporel directement dans le domaine de l'action
            action['domain'].append(('write_date', '>=', today_start))
            
            # On active le filtre "Ma Session" pour être sûr
            ctx.update({'search_default_my_session': 1})
            
            history_view = self.env.ref('repair_custom.view_repair_order_atelier_history_tree', raise_if_not_found=False)
            if history_view:
                # On dit à l'action : "Utilise cette vue Tree là, pas celle par défaut"
                action['views'] = [(history_view.id, 'tree'), (False, 'form')]
        
        if self.category_type == 'todo':
            # Active le filtre XML name="todo"
            ctx.update({'search_default_todo': 1})
            # On masque les brouillons via le domaine dur ici
            action['domain'].append(('state', '!=', 'draft'))
            
        elif self.category_type == 'progress':
            # Active le filtre XML name="in_progress"
            ctx.update({'search_default_in_progress': 1})
            
            # Active le filtre XML name="my_session"
            # Ce filtre va lire 'atelier_employee_id' qui est dans le ctx
            ctx.update({'search_default_my_session': 1})
            
            # Si le technicien crée une fiche depuis cette vue, on le pré-remplit
            if ctx.get('atelier_employee_id'):
                ctx.update({'default_technician_employee_id': ctx.get('atelier_employee_id')})
                
        elif self.category_type == 'waiting':
            ctx.update({'search_default_parts': 1})
            
        elif self.category_type == 'quote_waiting':
            ctx.update({'search_default_quote_waiting': 1})
        
        elif self.category_type == 'quote_validated':
            ctx.update({'search_default_quote_validated': 1})
            
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