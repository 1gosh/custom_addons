# -*- coding: utf-8 -*-
"""Main Repair Order model."""

from datetime import date, datetime, time, timedelta
from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.http import request
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

    waiting_time = fields.Char(
        string="Attente",
        compute='_compute_waiting_time',
        store=False,
    )

    @api.depends('entry_date', 'state')
    def _compute_waiting_time(self):
        today = fields.Date.today()
        for rec in self:
            if not rec.entry_date or rec.state in ('cancel',):
                rec.waiting_time = ''
                continue
            entry = rec.entry_date.date()
            delta = (today - entry).days
            if delta <= 0:
                rec.waiting_time = ''
            elif delta < 7:
                rec.waiting_time = f"{delta} j"
            elif delta < 30:
                rec.waiting_time = f"{round(delta / 7)} sem."
            else:
                rec.waiting_time = f"{round(delta / 30)} mois"

    @api.model
    def _default_location(self):
        """Default pickup location. JS overrides per-browser via localStorage."""
        location = self.env['repair.pickup.location'].search(
            [('name', '=', 'Boutique')], limit=1
        )
        if not location:
            location = self.env['repair.pickup.location'].search([], limit=1, order='name')
        return location.id if location else False

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
    active = fields.Boolean(default=True)
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
        ('pending', 'En préparation'),
        ('sent', 'Envoyé au client'),
        ('approved', 'Validé'),
        ('refused', 'Refusé')
    ], string="Statut Devis", default='none', tracking=True)

    # --- Quote lifecycle tracking fields (sub-project 2) ---
    quote_requested_date = fields.Datetime(
        string="Date demande devis",
        readonly=True, copy=False,
        help="Horodatage de l'appel à action_atelier_request_quote",
    )
    quote_sent_date = fields.Datetime(
        string="Date envoi devis",
        readonly=True, copy=False,
        help="Horodatage de la transition quote_state → sent",
    )
    last_reminder_sent_at = fields.Datetime(
        string="Dernière relance envoyée",
        readonly=True, copy=False,
    )
    contacted = fields.Boolean(
        string="Contacté hors système",
        default=False, copy=False,
        help="Flag consommé par le CRON après clic 'Contacté'",
    )
    contacted_at = fields.Datetime(
        string="Date du contact manuel",
        readonly=True, copy=False,
    )
    has_open_escalation = fields.Boolean(
        string="Escalade ouverte",
        compute='_compute_has_open_escalation',
        store=True,
    )
    has_open_refusal_activity = fields.Boolean(
        string="Activité de refus ouverte",
        compute='_compute_has_open_refusal_activity',
        store=True,
    )

    @api.depends('activity_ids.state', 'activity_ids.activity_type_id')
    def _compute_has_open_escalation(self):
        escalate_type = self.env.ref(
            'repair_custom.mail_act_repair_quote_escalate',
            raise_if_not_found=False,
        )
        for rec in self:
            if not escalate_type:
                rec.has_open_escalation = False
                continue
            rec.has_open_escalation = bool(rec.activity_ids.filtered(
                lambda a: a.activity_type_id == escalate_type and a.state != 'done'
            ))

    @api.depends('activity_ids.state', 'activity_ids.activity_type_id')
    def _compute_has_open_refusal_activity(self):
        refusal_type = self.env.ref(
            'repair_custom.mail_act_repair_quote_refused',
            raise_if_not_found=False,
        )
        for rec in self:
            if not refusal_type:
                rec.has_open_refusal_activity = False
                continue
            rec.has_open_refusal_activity = bool(rec.activity_ids.filtered(
                lambda a: a.activity_type_id == refusal_type and a.state != 'done'
            ))

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

    @api.depends('lot_id')
    def _compute_history_data(self):
        """Batch optimization: query all lots at once."""
        records_with_lots = self.filtered('lot_id')
        records_without_lots = self - records_with_lots

        records_without_lots.update({
            'history_repair_ids': False,
            'has_history': False,
            'previous_repair_id': False
        })

        if not records_with_lots:
            return

        lot_ids = records_with_lots.mapped('lot_id').ids
        current_repair_ids = [r.id for r in records_with_lots if isinstance(r.id, int)]

        domain = [('lot_id', 'in', lot_ids), ('state', '=', 'done')]
        if current_repair_ids:
            domain.append(('id', 'not in', current_repair_ids))

        all_repairs = self.env['repair.order'].with_context(active_test=False).search(domain, order='lot_id, end_date desc, write_date desc')

        repairs_by_lot = {}
        for repair in all_repairs:
            lot_id = repair.lot_id.id
            if lot_id not in repairs_by_lot:
                repairs_by_lot[lot_id] = []
            repairs_by_lot[lot_id].append(repair.id)

        for rec in records_with_lots:
            repair_ids = repairs_by_lot.get(rec.lot_id.id, [])
            rec.history_repair_ids = [(6, 0, repair_ids)] if repair_ids else False
            rec.has_history = bool(repair_ids)
            rec.previous_repair_id = repair_ids[0] if repair_ids else False

    def _get_sar_warranty_months(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_custom.sar_warranty_months', default='3'
        ))

    # --- Unit warranty related fields (for views) ---
    unit_warranty_type = fields.Selection(related='lot_id.warranty_type', store=False)
    unit_warranty_expiry = fields.Date(related='lot_id.warranty_expiry', store=False)
    unit_sale_date = fields.Datetime(related='lot_id.sale_date', store=False)

    def _compute_suggested_warranty(self):
        sar_months = self._get_sar_warranty_months()

        for rec in self:
            suggested = 'aucune'
            if rec.lot_id and rec.lot_id.warranty_state == 'active':
                suggested = rec.lot_id.warranty_type
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
                prev_repair = rec.previous_repair_id
                ref_date = prev_repair.end_date or prev_repair.write_date
                if ref_date:
                    limit_date = ref_date.date() + relativedelta(months=sar_months)
                    current_date = rec.entry_date.date() if rec.entry_date else fields.Date.today()
                    if current_date <= limit_date:
                        suggested = 'sar'

            rec.suggested_warranty = suggested

    suggested_warranty = fields.Selection([
        ('aucune', 'Aucune'),
        ('sar', 'SAR (Retour)'),
        ('sav', 'SAV'),
    ], string="Garantie Suggérée", compute='_compute_suggested_warranty', store=False)

    requires_ownership_transfer = fields.Boolean(
        compute='_compute_requires_ownership_transfer',
        help="Vrai quand le lot sélectionné appartient à un autre client que celui de la réparation.",
    )

    @api.depends('lot_id', 'lot_id.hifi_partner_id', 'partner_id')
    def _compute_requires_ownership_transfer(self):
        for rec in self:
            lot_owner = rec.lot_id.hifi_partner_id
            rec.requires_ownership_transfer = bool(
                lot_owner and rec.partner_id and lot_owner != rec.partner_id
            )

    @api.onchange('lot_id', 'entry_date')
    def _onchange_lot_workflow(self):
        """UI updates: Apply suggested warranty and show history popup."""
        if not self.lot_id:
            return

        self._compute_history_data()
        self._compute_suggested_warranty()

        lot_changed = (self.lot_id != self._origin.lot_id)
        if lot_changed or self.repair_warranty != 'sav':
            self.repair_warranty = self.suggested_warranty

        if not lot_changed:
            return

        lot = self.lot_id

        if (lot.hifi_partner_id and self.partner_id
                and lot.hifi_partner_id != self.partner_id):
            owner_name = lot.hifi_partner_id.name
            if lot.warranty_state == 'active':
                expiry_str = lot.warranty_expiry.strftime('%d/%m/%Y') if lot.warranty_expiry else '?'
                msg = _(
                    "Cet appareil appartient à %s (garantie %s active jusqu'au %s). "
                    "Cliquez sur « Transférer la propriété » pour l'associer à %s "
                    "et réinitialiser la garantie."
                ) % (owner_name, lot.warranty_type.upper(), expiry_str, self.partner_id.name)
            else:
                msg = _(
                    "Cet appareil appartient à %s. Cliquez sur « Transférer la propriété » "
                    "pour l'associer à %s."
                ) % (owner_name, self.partner_id.name)
            return {'warning': {
                'title': _("Changement de propriétaire requis"),
                'message': msg,
            }}

        if lot.warranty_state == 'active' and lot.warranty_type == 'sav':
            sale_date_str = lot.sale_date.strftime('%d/%m/%Y') if lot.sale_date else '?'
            expiry_str = lot.warranty_expiry.strftime('%d/%m/%Y') if lot.warranty_expiry else '?'
            return {'warning': {
                'title': _("Garantie SAV"),
                'message': _("Garantie SAV jusqu'au %s (Vendu le %s)") % (expiry_str, sale_date_str),
                'warning_type': 'notification',
            }}
        elif lot.warranty_state == 'active' and lot.warranty_type == 'sar':
            prev_repair = lot.last_delivered_repair_id or self.previous_repair_id
            tech_name = prev_repair.technician_employee_id.name if prev_repair and prev_repair.technician_employee_id else 'Inconnu'
            expiry_str = lot.warranty_expiry.strftime('%d/%m/%Y') if lot.warranty_expiry else '?'
            prev_date_str = (prev_repair.end_date or prev_repair.write_date).strftime('%d/%m/%Y')
            return {'warning': {
                'title': _("Retour Garantie (SAR)"),
                'message': _("Appareil sous garantie jusqu'au %s (Réparé par %s, le %s)") % (expiry_str, tech_name, prev_date_str),
                'warning_type': 'notification',
            }}
        elif self.previous_repair_id:
            prev_repair = self.previous_repair_id
            tech_name = prev_repair.technician_employee_id.name or 'Inconnu'
            prev_date_str = (prev_repair.end_date or prev_repair.write_date).strftime('%d/%m/%Y')
            return {'warning': {
                'title': _("Hors Garantie"),
                'message': _("Cet appareil a déjà été réparé par %s le %s (Garantie expirée)") % (tech_name, prev_date_str),
                'warning_type': 'notification',
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
    category_id = fields.Many2one('product.category', string="Catégorie")
    category_short_name = fields.Char(compute='_compute_category_short_name')

    @api.depends(
        'category_id',
        'category_id.name',
        'category_id.short_name',
        'category_id.parent_id',
        'category_id.parent_id.name',
        'category_id.parent_id.short_name',
    )
    def _compute_category_short_name(self):
        for rec in self:
            category = rec.category_id
            if not category:
                rec.category_short_name = False
                continue
            segments = []
            if category.parent_id:
                segments.append(category.parent_id.short_name or category.parent_id.name)
            segments.append(category.short_name or category.name)
            rec.category_short_name = ' / '.join(segments)
    product_tmpl_id = fields.Many2one('product.template', string="Modèle")
    variant_id = fields.Many2one('repair.device.variant', string="Variante")
    variant_ids_available = fields.Many2many('repair.device.variant', compute='_compute_variant_ids_available', store=False)

    @api.depends('product_tmpl_id', 'product_tmpl_id.hifi_variant_ids')
    def _compute_variant_ids_available(self):
        for rec in self:
            rec.variant_ids_available = rec.product_tmpl_id.hifi_variant_ids if rec.product_tmpl_id else False

    @api.onchange('product_tmpl_id')
    def _onchange_product_tmpl_id_set_category(self):
        self._onchange_product_tmpl_id_clear_variant()
        if self.product_tmpl_id and self.product_tmpl_id.categ_id:
            self.category_id = self.product_tmpl_id.categ_id

    lot_id = fields.Many2one(
        'stock.lot', string="Appareil physique",
        index=True,
        domain=[('is_hifi_unit', '=', True)],
        help="Unité physique. Tape un numéro de série existant pour le retrouver, "
             "ou un nouveau numéro pour le créer à la volée.",
    )
    product_variant_id = fields.Many2one(
        'product.product',
        related='product_tmpl_id.product_variant_id',
        store=False, readonly=True,
        string="Variante produit (pour contexte lot)",
    )
    device_id_name = fields.Char("Appareil", compute="_compute_device_id_name", readonly=True)
    show_lot_field = fields.Boolean(string="Afficher champ unité", compute="_compute_show_lot_field")

    @api.depends('lot_id', 'lot_id.product_id', 'lot_id.hifi_variant_id', 'product_tmpl_id', 'product_tmpl_id.display_name', 'variant_id')
    def _compute_device_id_name(self):
        for rec in self:
            if rec.lot_id:
                tmpl = rec.lot_id.product_id.product_tmpl_id
                name = tmpl.display_name or rec.lot_id.product_id.name or ""
                if rec.lot_id.hifi_variant_id:
                    name += f" ({rec.lot_id.hifi_variant_id.name})"
                rec.device_id_name = name
            elif rec.product_tmpl_id:
                name = rec.product_tmpl_id.display_name or ""
                if rec.variant_id:
                    name += f" ({rec.variant_id.name})"
                rec.device_id_name = name
            else:
                rec.device_id_name = _("Aucun modèle")

    @api.onchange('product_tmpl_id')
    def _onchange_product_tmpl_id_clear_variant(self):
        if self.lot_id and self.product_tmpl_id == self.lot_id.product_id.product_tmpl_id:
            return
        if self.product_tmpl_id:
            self.variant_id = False
            if self.lot_id:
                self.lot_id = False

    @api.onchange('category_id')
    def _onchange_category_id(self):
        if self.product_tmpl_id and self.category_id:
            product_cat = self.product_tmpl_id.categ_id
            if not product_cat or not (
                product_cat.parent_path
                and self.category_id.parent_path
                and product_cat.parent_path.startswith(self.category_id.parent_path)
            ):
                self.product_tmpl_id = False
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

    @api.onchange('lot_id')
    def _onchange_lot_id(self):
        for rec in self:
            if rec.lot_id:
                rec.product_tmpl_id = rec.lot_id.product_id.product_tmpl_id
                rec.variant_id = rec.lot_id.hifi_variant_id

    def action_open_unit(self):
        self.ensure_one()
        if not self.lot_id:
            raise UserError(_("Aucun appareil n'est associé à cette réparation."))
        return {
            'type': 'ir.actions.act_window',
            'name': 'Fiche Appareil',
            'res_model': 'stock.lot',
            'views': [(False, 'form')],
            'res_id': self.lot_id.id,
            'target': 'current',
        }

    @api.depends('lot_id', 'partner_id', 'state')
    def _compute_show_lot_field(self):
        Lot = self.env['stock.lot']
        for rec in self:
            show = False
            if rec.state == 'draft' and not rec.lot_id and rec.partner_id:
                show = bool(Lot.search([
                    ('hifi_partner_id', '=', rec.partner_id.id),
                    ('is_hifi_unit', '=', True),
                ], limit=1))
            rec.show_lot_field = show

    @api.onchange('partner_id')
    def _onchange_partner_clear_unit(self):
        if self.partner_id:
            self.lot_id = False

    # --- BATCH MANAGEMENT ---
    batch_id = fields.Many2one(
        'repair.batch', string="Dossier de Dépôt",
        readonly=True, index=True, ondelete='restrict',
    )
    batch_ready_for_pickup_notification = fields.Boolean(
        related='batch_id.ready_for_pickup_notification',
        store=False,
        string="Dossier prêt à notifier",
    )
    batch_delivery_state = fields.Selection(
        related='batch_id.delivery_state',
        store=False,
        string="État livraison dossier",
    )
    sibling_repair_ids = fields.Many2many(
        'repair.order',
        string="Autres réparations du dossier",
        compute='_compute_sibling_repair_ids',
    )
    has_siblings = fields.Boolean(
        compute='_compute_sibling_repair_ids',
    )
    batch_count = fields.Integer(
        compute='_compute_batch_count',
        string="Réparations dans le dossier",
    )

    @api.depends('batch_id.repair_ids')
    def _compute_batch_count(self):
        for rec in self:
            rec.batch_count = len(rec.batch_id.repair_ids) if rec.batch_id else 0

    def action_open_batch(self):
        self.ensure_one()
        if not self.batch_id:
            raise UserError(_("Cette réparation n'est pas liée à un dossier."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'repair.batch',
            'res_id': self.batch_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.depends('batch_id', 'batch_id.repair_ids')
    def _compute_sibling_repair_ids(self):
        for rec in self:
            if not rec.batch_id:
                rec.sibling_repair_ids = False
                rec.has_siblings = False
                continue
            peers = rec.batch_id.repair_ids - rec
            rec.sibling_repair_ids = peers
            rec.has_siblings = bool(peers)

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

    def action_notify_client_ready_from_repair(self):
        """Thin wrapper so repair form button can fire batch-level action."""
        self.ensure_one()
        return self.batch_id.action_notify_client_ready()

    def action_pickup_start(self):
        self.ensure_one()
        return self.batch_id.action_pickup_start()

    # --- WRITE OVERRIDE WITH SECURITY ---
    def write(self, vals):
        is_admin = self.env.user.has_group('repair_custom.group_repair_admin')
        is_manager = self.env.user.has_group('repair_custom.group_repair_manager')

        if not is_admin and not is_manager:
            protected_fields = {
                'tracking_token', 'invoice_ids', 'sale_order_id',
                'company_id', 'currency_id'
            }
            attempted = protected_fields & set(vals.keys())
            if attempted:
                raise UserError(
                    _("Vous n'avez pas les droits pour modifier ces champs : %s") % ', '.join(sorted(attempted))
                )

        if vals.get('state') == 'draft':
            vals = dict(vals)
            vals.update({'technician_user_id': False, 'technician_employee_id': False})

        res = super(Repair, self).write(vals)
        if 'active' in vals:
            batches = self.mapped('batch_id').exists()
            for batch in batches:
                all_children = batch.with_context(active_test=False).repair_ids
                active_children = all_children.filtered('active')
                if vals['active'] is False and not active_children and batch.active:
                    batch.active = False
                elif vals['active'] is True and active_children and not batch.active:
                    batch.active = True
        return res

    def unlink(self):
        batches = self.mapped('batch_id')
        res = super().unlink()
        for batch in batches.exists():
            if not batch.with_context(active_test=False).repair_ids.filtered('active'):
                batch.active = False
        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_confirmed(self):
        repairs_to_cancel = self.filtered(lambda ro: ro.state not in ('draft', 'cancel'))
        repairs_to_cancel.action_repair_cancel()

    # --- STOCK MOVE HELPER ---
    def _create_repair_picking(self, src_location, dest_location, origin=None):
        """Create, confirm, and validate a stock picking for this repair's lot."""
        self.ensure_one()
        if not self.lot_id or not self.lot_id.product_id:
            raise UserError(_("Impossible de créer un mouvement de stock : pas d'appareil associé."))

        product = self.lot_id.product_id
        if product.tracking != 'serial':
            product.tracking = 'serial'

        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.company_id.id)], limit=1)
        if not warehouse:
            raise UserError(_("Aucun entrepôt trouvé pour cette société."))

        # Select picking type based on location usage
        if src_location.usage == 'customer':
            picking_type = warehouse.in_type_id
        elif dest_location.usage == 'customer':
            picking_type = warehouse.out_type_id
        else:
            picking_type = warehouse.int_type_id

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': src_location.id,
            'location_dest_id': dest_location.id,
            'origin': origin or self.name,
            'partner_id': self.partner_id.id if self.partner_id else False,
        })

        move = self.env['stock.move'].create({
            'name': self.lot_id.display_name,
            'product_id': product.id,
            'product_uom': product.uom_id.id,
            'product_uom_qty': 1.0,
            'location_id': src_location.id,
            'location_dest_id': dest_location.id,
            'picking_id': picking.id,
        })

        move._action_confirm()
        move._action_assign()

        if move.move_line_ids:
            move.move_line_ids.write({'lot_id': self.lot_id.id, 'quantity': 1.0})
        else:
            self.env['stock.move.line'].create({
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': product.id,
                'product_uom_id': product.uom_id.id,
                'location_id': src_location.id,
                'location_dest_id': dest_location.id,
                'lot_id': self.lot_id.id,
                'quantity': 1.0,
            })

        picking.with_context(skip_backorder=True).button_validate()
        return picking

    # --- STATE TRANSITIONS ---
    def action_repair_cancel(self):
        if self.delivery_state == 'abandoned':
            raise UserError(_("Impossible de modifier l'état d'une réparation abandonnée."))
        admin = self.env.user.has_group('repair_custom.group_repair_admin')
        if not admin and any(repair.state == 'done' for repair in self):
            raise UserError(_("Impossible d'annuler une réparation terminée."))
        customer_location = self.env.ref('stock.stock_location_customers')
        for rec in self:
            if rec.state in ('confirmed', 'under_repair') and rec.lot_id:
                workshop = rec.pickup_location_id.stock_location_id
                if workshop and rec.lot_id.location_id == workshop:
                    rec._create_repair_picking(
                        workshop, customer_location,
                        origin=_("Annulation %s") % rec.name)
        return self.write({'state': 'cancel'})

    def action_repair_cancel_draft(self):
        if self.delivery_state == 'abandoned':
            raise UserError(_("Impossible de modifier l'état d'une réparation abandonnée."))
        if self.filtered(lambda repair: repair.state != 'cancel'):
            self.action_repair_cancel()
        return self.write({'state': 'draft', 'end_date': False})

    def action_repair_done(self):
        if any(r.delivery_state == 'abandoned' for r in self):
            raise UserError(_("Impossible de modifier l'état d'une réparation abandonnée."))
        for rec in self:
            try:
                self.env.cr.execute(
                    "SELECT id FROM repair_order WHERE id=%s FOR UPDATE NOWAIT",
                    (rec.id,),
                )
            except Exception:
                raise UserError(
                    _("La réparation %s est en cours de modification par un autre utilisateur.")
                    % rec.name
                )

        self.invalidate_recordset(['state', 'quote_state', 'quote_required'])

        if (not self.env.context.get('force_stop')
                and self.quote_required and self.quote_state != 'approved'):
            return {
                'name': _("Alerte : Devis non validé"),
                'type': 'ir.actions.act_window',
                'res_model': 'repair.warn.quote.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_repair_id': self.id},
            }

        res = self.write({
            'state': 'done',
            'parts_waiting': False,
            'end_date': fields.Datetime.now(),
        })

        quote_act_type = self.env.ref(
            'repair_custom.mail_act_repair_quote_validate', raise_if_not_found=False,
        )
        if quote_act_type:
            to_clean = self.activity_ids.filtered(
                lambda a: a.activity_type_id.id == quote_act_type.id
            )
            if to_clean:
                to_clean.action_feedback(
                    feedback="Clôture automatique : Réparation terminée."
                )

        # Sub-project 3: drop the legacy per-manager "Appareil Prêt" fan-out.
        # The new UX (batch-ready compute + notify dialog + fallback button)
        # replaces it.

        self.env.flush_all()
        self.mapped('batch_id').invalidate_recordset(
            ['ready_for_pickup_notification']
        )
        ready_batches = self.mapped('batch_id').filtered(
            'ready_for_pickup_notification'
        )
        # Only offer the dialog for single-record UI actions.
        if (ready_batches
                and not self.env.context.get('skip_pickup_notify_prompt')
                and len(ready_batches) == 1
                and len(self) == 1):
            return {
                'name': _("Dossier prêt pour retrait"),
                'type': 'ir.actions.act_window',
                'res_model': 'repair.pickup.notify.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_batch_id': ready_batches.id},
            }

        return res

    def action_repair_end(self):
        if self.delivery_state == 'abandoned':
            raise UserError(_("Impossible de modifier l'état d'une réparation abandonnée."))
        if self.filtered(lambda repair: repair.state != 'under_repair'):
            raise UserError(_("La réparation doit être en cours pour être terminée."))
        return self.action_repair_done()

    def action_set_irreparable(self):
        return self.write({'state': 'irreparable', 'end_date': fields.Datetime.now()})

    def action_repair_delivered(self):
        if self.filtered(lambda r: r.delivery_state == 'abandoned'):
            raise UserError(_("Impossible de livrer une réparation abandonnée. L'appareil est désormais propriété de l'atelier."))

        for rec in self:
            try:
                self.env.cr.execute("SELECT id FROM repair_order WHERE id=%s FOR UPDATE NOWAIT", (rec.id,))
            except Exception:
                raise UserError(_("La réparation %s est en cours de modification par un autre utilisateur.") % rec.name)

        self.invalidate_recordset(['state', 'delivery_state'])

        errors = []
        for rec in self:
            if rec.delivery_state == 'abandoned':
                errors.append(_("La réparation %s est abandonnée et ne peut pas être livrée.") % rec.name)
            elif rec.state not in ('done', 'irreparable'):
                errors.append(_("La réparation %s doit être 'Terminée' ou 'Irréparable' avant d'être livrée.") % rec.name)
            elif rec.delivery_state != 'none':
                errors.append(_("La réparation %s est déjà sortie de l'atelier.") % rec.name)

        if errors:
            raise UserError("\n".join(errors))

        self.write({
            'delivery_state': 'delivered',
            'end_date': fields.Datetime.now()
        })

        customer_location = self.env.ref('stock.stock_location_customers')
        for rec in self:
            if rec.lot_id:
                workshop = rec.pickup_location_id.stock_location_id
                if workshop and rec.lot_id.location_id == workshop:
                    rec._create_repair_picking(
                        workshop, customer_location,
                        origin=_("Livraison %s") % rec.name)

        sar_months = self._get_sar_warranty_months()
        for rec in self:
            if not rec.lot_id:
                continue
            if rec.state != 'done':
                # Irreparable: no warranty to grant. Still track the delivery.
                rec.lot_id.write({
                    'last_delivered_repair_id': rec.id,
                    'last_technician_id': rec.technician_employee_id.id,
                })
                continue
            sar_expiry = fields.Date.today() + relativedelta(months=sar_months)
            rec.lot_id.write({
                'last_delivered_repair_id': rec.id,
                'last_technician_id': rec.technician_employee_id.id,
                'sar_expiry': sar_expiry,
            })

        pickup_type_id = self.env.ref('repair_custom.mail_act_repair_done').id
        for rec in self:
            activities = rec.activity_ids.filtered(lambda a: a.activity_type_id.id == pickup_type_id)
            if activities:
                activities.action_feedback(feedback="Client livré (Appareil récupéré)")

        return True

    def action_repair_start(self):
        self.ensure_one()
        if self.delivery_state == 'abandoned':
            raise UserError(_("Impossible de modifier l'état d'une réparation abandonnée."))
        return self.write({'state': 'under_repair'})

    def _action_repair_confirm(self):
        if self.delivery_state == 'abandoned':
            raise UserError(_("Impossible de modifier l'état d'une réparation abandonnée."))
        Batch = self.env['repair.batch']
        for rec in self:
            if not rec.partner_id:
                raise UserError(_("Veuillez renseigner un client avant de confirmer la réparation."))
            if not rec.batch_id:
                rec.batch_id = Batch.create({
                    'partner_id': rec.partner_id.id,
                    'date': rec.entry_date or fields.Datetime.now(),
                    'company_id': rec.company_id.id,
                })
        return self.write({'state': 'confirmed'})

    def action_transfer_ownership(self):
        """Transfer the selected lot to the repair's customer and reset sale/warranty data.

        The original sale and warranty fields are archived into the lot's chatter
        before being cleared, so the history remains auditable.
        """
        self.ensure_one()
        lot = self.lot_id
        if not lot or not self.partner_id:
            return
        old_partner = lot.hifi_partner_id
        if not old_partner or old_partner == self.partner_id:
            return

        sale_date_str = lot.sale_date.strftime('%d/%m/%Y') if lot.sale_date else '—'
        sale_order_ref = lot.sale_order_id.name if lot.sale_order_id else '—'
        warranty_type = (lot.warranty_type or 'none').upper()
        warranty_expiry_str = lot.warranty_expiry.strftime('%d/%m/%Y') if lot.warranty_expiry else '—'
        last_repair_ref = lot.last_delivered_repair_id.name if lot.last_delivered_repair_id else '—'
        last_tech = lot.last_technician_id.name if lot.last_technician_id else '—'

        body = _(
            "Propriété transférée de <strong>%(old)s</strong> vers <strong>%(new)s</strong> "
            "via la réparation %(ref)s.<br/>"
            "Données précédentes archivées : vente du %(sale_date)s (commande %(sale_order)s), "
            "garantie %(warranty_type)s jusqu'au %(warranty_expiry)s, "
            "dernière réparation %(last_repair)s par %(last_tech)s.<br/>"
            "Garantie et liens de vente réinitialisés."
        ) % {
            'old': old_partner.display_name,
            'new': self.partner_id.display_name,
            'ref': self.name,
            'sale_date': sale_date_str,
            'sale_order': sale_order_ref,
            'warranty_type': warranty_type,
            'warranty_expiry': warranty_expiry_str,
            'last_repair': last_repair_ref,
            'last_tech': last_tech,
        }
        lot.message_post(body=body)

        lot.write({
            'hifi_partner_id': self.partner_id.id,
            'sale_date': False,
            'sale_order_id': False,
            'sav_expiry': False,
            'sar_expiry': False,
            'last_delivered_repair_id': False,
            'last_technician_id': False,
        })
        self._onchange_lot_workflow()

    def action_validate(self):
        """Confirm repair, create stock.lot if needed, and create intake stock move."""
        self.ensure_one()
        if self.delivery_state == 'abandoned':
            raise UserError(_("Impossible de modifier l'état d'une réparation abandonnée."))
        if self.requires_ownership_transfer:
            raise UserError(_(
                "L'appareil sélectionné appartient à un autre client. "
                "Cliquez sur « Transférer la propriété » avant de confirmer la réparation."
            ))
        if self.variant_id and self.variant_id not in self.product_tmpl_id.hifi_variant_ids:
            self.product_tmpl_id.write({'hifi_variant_ids': [(4, self.variant_id.id)]})

        workshop_location = self.pickup_location_id.stock_location_id
        if not workshop_location:
            raise UserError(_("Le lieu de prise en charge '%s' n'a pas d'emplacement de stock configuré.") % self.pickup_location_id.name)
        customer_location = self.env.ref('stock.stock_location_customers')
        Quant = self.env['stock.quant']

        if self.lot_id:
            # Existing lot — move to workshop if not already there
            if self.lot_id.location_id != workshop_location:
                product = self.lot_id.product_id
                # Seed quant at customer location if lot has no positive quant
                if not Quant.search([('lot_id', '=', self.lot_id.id), ('quantity', '>', 0)], limit=1):
                    Quant._update_available_quantity(product, customer_location, 1.0, lot_id=self.lot_id)
                self._create_repair_picking(customer_location, workshop_location)
            else:
                self.message_post(body=_("Appareil déjà présent à l'atelier, pas de mouvement de stock créé."))
            return self._action_repair_confirm()

        if self.product_tmpl_id and self.partner_id:
            # Fallback lot creation for programmatic callers / imports that
            # didn't set lot_id. Interactive users now set lot_id directly.
            product = self.product_tmpl_id.product_variant_id
            if not product:
                raise UserError(_("Aucun produit trouvé pour cet appareil."))
            lot_vals = {
                'name': f"REP-{self.name}",
                'product_id': product.id,
                'company_id': self.company_id.id,
                'hifi_partner_id': self.partner_id.id,
            }
            if self.variant_id:
                lot_vals['hifi_variant_id'] = self.variant_id.id
            new_lot = self.env['stock.lot'].create(lot_vals)
            self.write({'lot_id': new_lot.id})
            Quant._update_available_quantity(product, customer_location, 1.0, lot_id=new_lot)
            self._create_repair_picking(customer_location, workshop_location)
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
    is_quote_invoiceable = fields.Boolean(
        compute='_compute_is_quote_invoiceable',
        string="Devis facturable",
    )

    @api.depends('sale_order_id')
    def _compute_sale_order_count(self):
        for rec in self:
            rec.sale_order_count = 1 if rec.sale_order_id else 0

    @api.depends('quote_state', 'sale_order_id.invoice_status')
    def _compute_is_quote_invoiceable(self):
        for rec in self:
            rec.is_quote_invoiceable = (
                rec.quote_state == 'approved'
                and bool(rec.sale_order_id)
                and rec.sale_order_id.invoice_status in ('to invoice', 'upselling')
            )

    def action_invoice_repair_quote(self):
        """Per-repair invoicing. Delegates to the batch helper with self as
        the singleton repair set."""
        self.ensure_one()
        if not self.batch_id:
            raise UserError(_("Cette réparation n'est rattachée à aucun dossier."))
        return self.batch_id._invoice_approved_quotes(self)

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

    def action_open_abandon_wizard(self):
        self.ensure_one()
        if self.state == 'draft':
            raise UserError(_("Impossible d'abandonner un appareil en brouillon."))
        if self.delivery_state != 'none':
            raise UserError(_("L'appareil est déjà sorti de l'atelier."))
        if not self.lot_id:
            raise UserError(_("Aucun appareil associé à cette réparation."))
        return {
            'name': _("Abandon & Entrée en Stock"),
            'type': 'ir.actions.act_window',
            'res_model': 'device.stock.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_lot_id': self.lot_id.id,
                'default_repair_id': self.id,
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
    @api.constrains('batch_id', 'state')
    def _check_batch_id_required(self):
        for rec in self:
            if rec.state != 'draft' and not rec.batch_id:
                raise ValidationError(_(
                    "Un dossier de dépôt est obligatoire une fois la réparation confirmée."
                ))

    @api.constrains('lot_id', 'product_tmpl_id', 'variant_id')
    def _check_unit_consistency(self):
        for rec in self:
            if rec.lot_id:
                lot_tmpl = rec.lot_id.product_id.product_tmpl_id
                if rec.product_tmpl_id and rec.product_tmpl_id != lot_tmpl:
                    raise ValidationError(_("Incohérence Modèle !"))
                if rec.lot_id.hifi_variant_id and rec.variant_id and rec.variant_id != rec.lot_id.hifi_variant_id:
                    raise ValidationError(_("Incohérence Variante !"))

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
        if not self.technician_employee_id:
            if self.env.context.get('atelier_employee_id'):
                self.technician_employee_id = self.env.context.get('atelier_employee_id')
            elif not self.env.user.share:
                employee = self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1)
                if employee:
                    self.technician_employee_id = employee.id

    def action_atelier_start(self):
        self.ensure_one()

        if self.delivery_state == 'abandoned':
            raise UserError(_("Impossible de modifier l'état d'une réparation abandonnée."))

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
        self.ensure_one()
        self._assign_technician_if_needed()

        if not self.internal_notes:
            raise UserError(_("Veuillez remplir l'estimation technique avant de demander un devis."))

        self._apply_quote_state_transition('pending')
        self.quote_requested_date = fields.Datetime.now()
        tech_name = (self.technician_employee_id.name
                     if self.technician_employee_id else self.env.user.name)
        self.message_post(body=_("🔖 Devis demandé par %s.") % tech_name)
        return True

    def action_create_quotation_wizard(self):
        self.ensure_one()
        device_categ_id = self.product_tmpl_id.categ_id.id if self.product_tmpl_id else False

        return {
            'name': _("Création du Devis"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.pricing.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_repair_id': self.id,
                'default_device_categ_id': device_categ_id,
            },
        }

    def action_manager_validate_quote(self):
        self.ensure_one()

        target_type_id = self.env.ref('repair_custom.mail_act_repair_quote_validate').id
        activities = self.activity_ids.filtered(lambda a: a.activity_type_id.id == target_type_id)
        if activities:
            activities.action_feedback(feedback=f"Validé par {self.env.user.name}")

        self.message_post(body="Devis validé par le management.")
        return self.write({'quote_state': 'approved'})

    # ============================================================
    # Quote lifecycle (sub-project 2)
    # ============================================================

    def _apply_quote_state_transition(self, new_state, from_sale_order=False):
        """Single entry point for all `quote_state` transitions.

        Called from:
        - action_atelier_request_quote (tech button)
        - sale.order.write() override (sale.order state sync)
        - _cron_process_pending_quotes (CRON)
        - action_quote_contacted (manager button)

        Handles side effects:
        - Chatter messages
        - Activity creation/closure
        - Tech notifications
        - Date stamping (quote_sent_date)
        """
        # Detect portal actions reliably. The Odoo portal flow calls
        # `sale.order.sudo().action_confirm()` from the portal controller,
        # which masks `self.env.user` as OdooBot (non-share). The only
        # trustworthy signal is the HTTP request's authenticated user,
        # which remains the portal/public user throughout the sudo call.
        http_user = request.env.user if request else None
        is_portal_action = bool(
            from_sale_order and http_user and http_user.share
        )
        # For manual-path chatter messages, prefer the HTTP user over
        # `self.env.user` so we don't attribute backend sudo calls to
        # OdooBot when a real user is logged in.
        actor = http_user if http_user and not http_user.share else self.env.user

        for rec in self:
            old = rec.quote_state
            if old == new_state:
                continue
            rec.quote_state = new_state

            if new_state == 'sent':
                rec.quote_sent_date = fields.Datetime.now()
                rec.message_post(body=_("📧 Devis envoyé au client."))

            elif new_state == 'approved':
                if is_portal_action:
                    rec.message_post(body=_(
                        "✅ Devis accepté par le client via le portail."
                    ))
                else:
                    rec.message_post(body=_(
                        "✅ Devis validé manuellement par %s."
                    ) % actor.name)
                rec._notify_tech_quote_approved()
                rec._close_escalation_activities()

            elif new_state == 'refused':
                if is_portal_action:
                    rec.message_post(body=_(
                        "❌ Devis refusé par le client via le portail."
                    ))
                else:
                    rec.message_post(body=_(
                        "❌ Devis annulé manuellement par %s."
                    ) % actor.name)
                rec._create_refusal_activity()
                rec._close_escalation_activities()

            elif new_state == 'pending' and old in ('sent', 'approved', 'refused'):
                rec.message_post(body=_("↩ Devis remis en préparation."))

    def _notify_tech_quote_approved(self):
        """Post a chatter message with a mention of the technician when possible."""
        for rec in self:
            tech = rec.technician_employee_id
            if tech and tech.user_id:
                rec.message_post(
                    body=_("✅ Devis validé. @%s peut reprendre l'intervention.") % tech.name,
                    partner_ids=[tech.user_id.partner_id.id],
                )
            else:
                rec.message_post(body=_(
                    "✅ Devis validé. Le technicien peut reprendre l'intervention."
                ))

    def _close_escalation_activities(self):
        """Mark all open escalation activities on these repairs as done."""
        escalate_type = self.env.ref(
            'repair_custom.mail_act_repair_quote_escalate',
            raise_if_not_found=False,
        )
        if not escalate_type:
            return
        for rec in self:
            activities = rec.activity_ids.filtered(
                lambda a: a.activity_type_id == escalate_type and a.state != 'done'
            )
            if activities:
                activities.action_feedback(feedback=_("Fermée automatiquement (changement d'état du devis)"))

    def _create_refusal_activity(self):
        """Create a 'statuer' activity for each manager in the repair group."""
        refusal_type = self.env.ref(
            'repair_custom.mail_act_repair_quote_refused',
            raise_if_not_found=False,
        )
        manager_group = self.env.ref(
            'repair_custom.group_repair_manager',
            raise_if_not_found=False,
        )
        if not refusal_type or not manager_group:
            return
        for rec in self:
            for manager_user in manager_group.users:
                rec.activity_schedule(
                    activity_type_id=refusal_type.id,
                    user_id=manager_user.id,
                    summary=_("Devis refusé — statuer sur la réparation"),
                    note=_(
                        "Le devis pour %s a été refusé. Action requise (retrait, nouveau devis, annulation…)."
                    ) % (rec.device_id_name or rec.name),
                    date_deadline=fields.Date.today(),
                )

    def action_quote_contacted(self):
        """Manager button: mark the client as contacted, close escalation activities,
        reset the CRON escalation clock.
        """
        for rec in self:
            rec._close_escalation_activities()
            rec.contacted = True
            rec.contacted_at = fields.Datetime.now()
            rec.message_post(body=_("📞 Contacté par %s") % self.env.user.name)
        return True

    def _send_quote_reminder_mail(self):
        """Send the reminder mail template to the client."""
        template = self.env.ref(
            'repair_custom.mail_template_repair_quote_reminder',
            raise_if_not_found=False,
        )
        if not template:
            return
        for rec in self:
            template.send_mail(rec.id, force_send=False)

    @api.model
    def _cron_process_pending_quotes(self):
        """Hourly CRON: reminder + escalation cascade for sent quotes."""
        today = fields.Datetime.now()
        Params = self.env['ir.config_parameter'].sudo()
        reminder_delay = int(Params.get_param('repair_custom.quote_reminder_delay_days', 5))
        escalation_delay = int(Params.get_param('repair_custom.quote_escalation_delay_days', 3))

        sent_repairs = self.search([
            ('quote_state', '=', 'sent'),
            ('quote_sent_date', '!=', False),
        ])

        for repair in sent_repairs:
            # Phase 1: the single reminder mail
            if (not repair.last_reminder_sent_at
                    and not repair.contacted
                    and today >= repair.quote_sent_date + timedelta(days=reminder_delay)):
                repair._send_quote_reminder_mail()
                repair.last_reminder_sent_at = today
                continue

            # Phase 2: escalation activity
            if repair.has_open_escalation:
                continue

            if repair.contacted:
                if repair.contacted_at and today >= repair.contacted_at + timedelta(days=escalation_delay):
                    repair._create_quote_escalation_activity()
                    repair.contacted = False
            elif repair.last_reminder_sent_at:
                if today >= repair.last_reminder_sent_at + timedelta(days=escalation_delay):
                    repair._create_quote_escalation_activity()

    def _create_quote_escalation_activity(self):
        """Create one escalation activity per manager in group_repair_manager."""
        escalate_type = self.env.ref(
            'repair_custom.mail_act_repair_quote_escalate',
            raise_if_not_found=False,
        )
        manager_group = self.env.ref(
            'repair_custom.group_repair_manager',
            raise_if_not_found=False,
        )
        if not escalate_type or not manager_group:
            return
        for rec in self:
            for manager_user in manager_group.users:
                note_lines = [
                    _("Devis envoyé le %s, toujours pas de réponse client.") % (
                        rec.quote_sent_date.strftime('%d/%m/%Y') if rec.quote_sent_date else '?'
                    ),
                    _("Téléphone client : %s") % (rec.partner_id.phone or '?'),
                ]
                if rec.sale_order_id:
                    note_lines.append(_("Devis : %s") % rec.sale_order_id.name)
                rec.activity_schedule(
                    activity_type_id=escalate_type.id,
                    user_id=manager_user.id,
                    summary=_("Client à contacter — devis non validé"),
                    note="<br/>".join(note_lines),
                    date_deadline=fields.Date.today(),
                )

    def action_atelier_parts_toggle(self):
        for rec in self:
            rec.parts_waiting = not rec.parts_waiting
            msg = "Pièces commandées / En attente." if rec.parts_waiting else "Pièces reçues."
            rec.message_post(body=msg)
        return True

    def action_atelier_abort(self):
        self.ensure_one()

        if self.delivery_state == 'abandoned':
            raise UserError(_("Impossible de modifier l'état d'une réparation abandonnée."))

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
                'default_category_id': self.category_id.id
            }
        }

    def action_merge_into_batch(self):
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
