from random import randint
from datetime import date
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

    multiple_devices = fields.Boolean(string="Plusieurs appareils")
    repair_warranty = fields.Selection([('aucune', 'Aucune'), ('sav', 'SAV'), ('sar', 'SAR'),], string="Garantie", default='aucune')
    notes = fields.Text(string="Notes additionnelles")
    
    technician_user_id = fields.Many2one(
        'res.users',
        string="Technicien (Utilisateur)",
        readonly=True,
        help="Utilisateur Odoo ayant démarré la réparation."
    )
    technician_employee_id = fields.Many2one(
        'hr.employee',
        string="Technicien",
        readonly=True,
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

    # --- Appareil lié à la réparation ---
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
    def _onchange_device_id_clear_variant(self):
        if self.device_id:
            self.variant_id = False
            
    serial_number = fields.Char(
        "N° de série",
        related="unit_id.serial_number",
        store=True,
        readonly=False,
        help="Numéro de série de l'appareil lié. Si aucune unité n'est encore créée, il sera rempli lors de la confirmation."
    )
    device_id_name = fields.Char(
        "Appareil",
        related="unit_id.device_name",
        store=True,
        readonly=False
    )
    unit_id = fields.Many2one(
        'repair.device.unit',
        string="Appareil (unité physique)",
        readonly=True,
        domain="[('device_id', '=', device_id), ('partner_id', '=', partner_id)]",
        help="Appareil physique unique correspondant au modèle/variante/numéro de série."
    )
    tag_ids = fields.Many2many('repair.tags', string="Tags")
    internal_notes = fields.Text("Notes de réparation")

    def action_create_device(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Créer un modèle d’appareil',
            'res_model': 'repair.device',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_brand_id': False},
        }

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
            # Par défaut, caché
            show = False

            if rec.state == 'draft':
                # visible seulement en brouillon et si le partenaire a des unités
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
    sale_order_line_id = fields.Many2one(
        'sale.order.line', check_company=True, readonly=True,
        copy=False, help="Sale Order Line from which the Repair Order comes from.")


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

    def action_create_sale_order(self):
        if any(repair.sale_order_id for repair in self):
            concerned_ro = self.filtered('sale_order_id')
            ref_str = "\n".join(ro.name for ro in concerned_ro)
            raise UserError(_("You cannot create a quotation for a repair order that is already linked to an existing sale order.\nConcerned repair order(s) :\n") + ref_str)
        if any(not repair.partner_id for repair in self):
            concerned_ro = self.filtered(lambda ro: not ro.partner_id)
            ref_str = "\n".join(ro.name for ro in concerned_ro)
            raise UserError(_("You need to define a customer for a repair order in order to create an associated quotation.\nConcerned repair order(s) :\n") + ref_str)
        sale_order_values_list = []
        for repair in self:
            sale_order_values_list.append({
                "company_id": repair.company_id.id,
                "partner_id": repair.partner_id.id,
                "repair_order_ids": [Command.link(repair.id)],
            })
        self.env['sale.order'].create(sale_order_values_list)
        return self.action_view_sale_order()

    def action_repair_cancel(self):
        if any(repair.state == 'done' for repair in self):
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
        res = self.write({'state': 'under_repair'})

        user = self.env.user
        employee = self.env['hr.employee'].search([('user_id', '=', user.id)], limit=1)

        self.write({
            'technician_user_id': user.id,
            'technician_employee_id': employee.id if employee else False,
        })

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

        # Si une variante a été saisie manuellement, l'associer au modèle si nécessaire
        if self.variant_id and self.variant_id not in self.device_id.variant_ids:
            self.device_id.write({'variant_ids': [(4, self.variant_id.id)]})

        # 👉 S’il y a déjà une unité sélectionnée manuellement → ne rien créer
        if self.unit_id:
            return self._action_repair_confirm()

        # 👉 Sinon, créer une nouvelle unité automatiquement
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
            self.unit_id = new_unit

        return self._action_repair_confirm() 

    def action_view_sale_order(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "views": [[False, "form"]],
            "res_id": self.sale_order_id.id,
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('repair.order') or 'New'
        return super(Repair, self).create(vals_list)

    def print_repair_order(self):
        return self.env.ref('repair.action_report_repair_order').report_action(self)


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

    name = fields.Char('Tag Name', required=True)
    color = fields.Integer(string='Color Index', default=_get_default_color)

    _sql_constraints = [
        ('name_uniq', 'unique (name)', "Tag name already exists!"),
    ]

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