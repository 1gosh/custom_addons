from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta


class StockLot(models.Model):
    _inherit = 'stock.lot'

    # Repair tracking
    repair_order_ids = fields.One2many('repair.order', 'lot_id', string="Réparations associées")
    repair_order_count = fields.Integer(string="Réparations", compute='_compute_repair_order_count')

    # Functional state (computed from repair history)
    functional_state = fields.Selection([
        ('broken', 'En panne'),
        ('fixing', 'En Atelier'),
        ('working', 'Réparé'),
    ], string="État physique", compute='_compute_functional_state', store=True)

    # Stock state — TEMPORARY in Phase 1, replaced by location queries in Phase 2
    stock_state = fields.Selection([
        ('client', 'Propriété Client'),
        ('stock', 'En Stock'),
        ('in_repair', 'En Réparation'),
        ('sold', 'Vendu'),
        ('rented', 'En Location'),
    ], string="Statut Stock", default='client', tracking=True)

    # SAV warranty (equipment sale)
    sale_date = fields.Datetime("Date de vente", readonly=True, copy=False)
    sav_expiry = fields.Date("Expiration SAV", readonly=True, copy=False)
    sale_order_id = fields.Many2one('sale.order', string="Commande de vente", readonly=True, copy=False)

    # SAR warranty (repair)
    last_delivered_repair_id = fields.Many2one('repair.order', string="Dernière réparation livrée", readonly=True, copy=False)
    sar_expiry = fields.Date("Expiration SAR", readonly=True, copy=False)

    # Computed warranty info (SAV priority over SAR)
    warranty_type = fields.Selection([
        ('none', 'Aucune'),
        ('sar', 'SAR'),
        ('sav', 'SAV'),
    ], string="Type de garantie", compute='_compute_warranty_info', store=False)
    warranty_expiry = fields.Date("Expiration garantie", compute='_compute_warranty_info', store=False)
    warranty_state = fields.Selection([
        ('none', 'Aucune'),
        ('active', 'Active'),
        ('expired', 'Expirée'),
    ], string="État garantie", compute='_compute_warranty_info', store=False)

    is_admin = fields.Boolean(
        compute="_compute_is_admin",
        string="Administrateur",
        store=False,
    )

    def _compute_warranty_info(self):
        today = fields.Date.today()
        for unit in self:
            w_type = 'none'
            w_expiry = False
            w_state = 'none'

            if unit.sav_expiry and unit.sav_expiry >= today:
                w_type = 'sav'
                w_expiry = unit.sav_expiry
                w_state = 'active'
            elif unit.sar_expiry and unit.sar_expiry >= today:
                w_type = 'sar'
                w_expiry = unit.sar_expiry
                w_state = 'active'
            elif unit.sav_expiry or unit.sar_expiry:
                w_state = 'expired'
                w_expiry = max(filter(None, [unit.sav_expiry, unit.sar_expiry]))

            unit.warranty_type = w_type
            unit.warranty_expiry = w_expiry
            unit.warranty_state = w_state

    def _compute_repair_order_count(self):
        for rec in self:
            rec.repair_order_count = self.env['repair.order'].search_count([('lot_id', '=', rec.id)])

    @api.depends('repair_order_ids.state')
    def _compute_functional_state(self):
        for unit in self:
            active_repairs = unit.repair_order_ids.filtered(
                lambda r: r.state in ['confirmed', 'under_repair']
            )
            if active_repairs:
                unit.functional_state = 'fixing'
            else:
                last_repair = unit.repair_order_ids.sorted(
                    key=lambda r: r.end_date or r.write_date,
                    reverse=True
                )
                if last_repair and last_repair[0].state in ['done']:
                    unit.functional_state = 'working'
                else:
                    unit.functional_state = 'broken'

    def _compute_is_admin(self):
        user = self.env.user
        for rec in self:
            rec.is_admin = user.has_group('repair_custom.group_repair_admin')

    def action_view_repairs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Réparations',
            'res_model': 'repair.order',
            'view_mode': 'tree,form',
            'domain': [('lot_id', '=', self.id)],
            'context': {'default_lot_id': self.id},
        }

    def action_open_stock_wizard(self):
        self.ensure_one()
        return {
            'name': _("Entrée en Stock"),
            'type': 'ir.actions.act_window',
            'res_model': 'device.stock.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_lot_id': self.id},
        }


class AccountMove(models.Model):
    _inherit = 'account.move'

    repair_id = fields.Many2one('repair.order', string="Réparation d'origine", readonly=True)
    batch_id = fields.Many2one(r'repair.batch', string="Dossier de réparation d'origine", readonly=True)
    repair_notes = fields.Text(related='repair_id.internal_notes', string="Notes de l'atelier", readonly=True)

    def _is_equipment_sale_invoice(self):
        self.ensure_one()
        equipment_fp = self.env.ref(
            'repair_custom.fiscal_position_equipment_sale',
            raise_if_not_found=False
        )
        if equipment_fp and self.fiscal_position_id == equipment_fp:
            return True
        sale_orders = self.invoice_line_ids.mapped('sale_line_ids.order_id')
        if sale_orders:
            return any(order._is_equipment_sale() for order in sale_orders)
        return False


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    repair_order_ids = fields.One2many(
        comodel_name='repair.order', inverse_name='sale_order_id',
        string='Repair Order', groups='stock.group_stock_user')
    repair_count = fields.Integer(
        "Repair Order(s)", compute='_compute_repair_count', groups='stock.group_stock_user')

    computed_order_type = fields.Selection([
        ('standard', 'Commande Standard'),
        ('repair_quote', 'Devis Réparation'),
        ('equipment_sale', 'Vente Équipement'),
        ('rental', 'Location'),
    ], string="Type de commande", compute='_compute_order_type', store=True)

    @api.depends('sale_order_template_id', 'sale_order_template_id.template_type')
    def _compute_order_type(self):
        for order in self:
            if order.sale_order_template_id:
                order.computed_order_type = order.sale_order_template_id.template_type
            else:
                order.computed_order_type = 'standard'

    fiscal_position_id = fields.Many2one(
        'account.fiscal.position',
        string="Position Fiscale",
        compute='_compute_fiscal_position_from_template',
        store=True,
        readonly=False,
        check_company=True,
        help="Position fiscale appliquée automatiquement selon le type de commande"
    )

    @api.depends('computed_order_type')
    def _compute_fiscal_position_from_template(self):
        for order in self:
            if order.state not in ['draft', 'sent']:
                continue
            if order.computed_order_type == 'repair_quote':
                order.fiscal_position_id = self.env.ref(
                    'repair_custom.fiscal_position_repair', raise_if_not_found=False)
            elif order.computed_order_type == 'equipment_sale':
                order.fiscal_position_id = self.env.ref(
                    'repair_custom.fiscal_position_equipment_sale', raise_if_not_found=False)
            elif order.computed_order_type == 'rental':
                order.fiscal_position_id = self.env.ref(
                    'repair_custom.fiscal_position_rental', raise_if_not_found=False)
            else:
                if not order.fiscal_position_id:
                    order.fiscal_position_id = False

    def _is_rental(self):
        self.ensure_one()
        return self.computed_order_type == 'rental'

    def _is_equipment_sale(self):
        self.ensure_one()
        return self.computed_order_type == 'equipment_sale'

    def _is_repair_quote(self):
        self.ensure_one()
        return self.computed_order_type == 'repair_quote'

    def _requires_special_stock_handling(self):
        self.ensure_one()
        return self.computed_order_type in ('rental', 'equipment_sale')

    # --- Rental fields ---
    rental_start_date = fields.Datetime("Date début location")
    rental_end_date = fields.Datetime("Date fin location")
    rental_return_date = fields.Date("Date retour effectif")
    rental_state = fields.Selection([
        ('draft', 'Brouillon'),
        ('active', 'En cours'),
        ('returned', 'Retourné'),
        ('overdue', 'En retard'),
    ], string="État location", default='draft', tracking=True)
    rental_notes = fields.Text("Notes location")

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

    def _get_hifi_lots_from_lines(self):
        """Get HiFi stock.lot records from order lines."""
        return self.order_line.mapped('lot_id').filtered(
            lambda l: l and l.is_hifi_unit
        )

    def action_confirm(self):
        """Override to handle equipment sales and rentals."""
        for order in self:
            if order._is_rental():
                if not order.rental_start_date or not order.rental_end_date:
                    raise UserError(_("Veuillez définir les dates de location avant de confirmer."))
                if order.rental_end_date < order.rental_start_date:
                    raise UserError(_("La date de fin doit être postérieure à la date de début."))

        res = super().action_confirm()

        for order in self:
            lots = order._get_hifi_lots_from_lines()

            if order._is_rental():
                lots.write({'stock_state': 'rented'})
                order.rental_state = 'active'

                rented_location = self.env.ref('repair_custom.stock_location_rented', raise_if_not_found=False)
                if not rented_location:
                    raise UserError(_(
                        "Configuration manquante: la location 'Appareils en Location' n'existe pas.\n"
                        "Veuillez réinstaller le module repair_custom."
                    ))

                warehouse = order.warehouse_id or self.env['stock.warehouse'].search([
                    ('company_id', '=', order.company_id.id)
                ], limit=1)
                if not warehouse:
                    raise UserError(_(
                        "Aucun entrepôt trouvé pour cette société.\n"
                        "Veuillez configurer un entrepôt avant de confirmer des locations."
                    ))

                stock_location = warehouse.lot_stock_id
                internal_type = warehouse.int_type_id

                picking_vals = {
                    'picking_type_id': internal_type.id,
                    'location_id': stock_location.id,
                    'location_dest_id': rented_location.id,
                    'origin': order.name,
                    'partner_id': order.partner_id.id,
                    'sale_id': order.id,
                }
                picking = self.env['stock.picking'].create(picking_vals)

                for line in order.order_line.filtered(lambda l: l.lot_id and l.lot_id.is_hifi_unit):
                    move_vals = {
                        'name': line.name,
                        'product_id': line.product_id.id,
                        'product_uom': line.product_uom.id,
                        'product_uom_qty': 1.0,
                        'location_id': stock_location.id,
                        'location_dest_id': rented_location.id,
                        'picking_id': picking.id,
                    }

                    if line.lot_id:
                        move_vals['lot_ids'] = [(4, line.lot_id.id)]

                    move = self.env['stock.move'].create(move_vals)
                    move._action_confirm()
                    move._action_assign()

                    if move.state != 'assigned':
                        raise UserError(_(
                            "Stock insuffisant pour la location.\n"
                            "Appareil: %s\n"
                            "L'appareil doit être disponible en stock avant de confirmer la location."
                        ) % line.lot_id.display_name)

                    if line.lot_id and move.move_line_ids:
                        move.move_line_ids.write({'lot_id': line.lot_id.id})

                if all(move.state == 'assigned' for move in picking.move_ids):
                    picking.button_validate()

            elif order._is_equipment_sale():
                lots.write({
                    'stock_state': 'sold',
                    'hifi_partner_id': order.partner_id.id,
                })

                sav_months = int(self.env['ir.config_parameter'].sudo().get_param(
                    'repair_custom.sav_warranty_months', default='12'))
                sav_expiry = fields.Date.today() + relativedelta(months=sav_months)
                for lot in lots:
                    lot.write({
                        'sale_date': fields.Datetime.now(),
                        'sav_expiry': sav_expiry,
                        'sale_order_id': order.id,
                    })

            if order._requires_special_stock_handling():
                pickings = order.picking_ids.filtered(
                    lambda p: p.state not in ['done', 'cancel']
                )
                for picking in pickings:
                    if all(move.state == 'assigned' for move in picking.move_ids):
                        picking.button_validate()

        return res

    def action_return_rental(self):
        """Mark rental as returned and restore stock with internal transfer."""
        for order in self:
            if not order._is_rental():
                continue

            lots = order._get_hifi_lots_from_lines()
            lots.write({'stock_state': 'stock'})

            rented_location = self.env.ref('repair_custom.stock_location_rented', raise_if_not_found=False)
            warehouse = order.warehouse_id or self.env['stock.warehouse'].search([
                ('company_id', '=', order.company_id.id)
            ], limit=1)

            if rented_location and warehouse:
                stock_location = warehouse.lot_stock_id
                internal_type = warehouse.int_type_id

                picking_vals = {
                    'picking_type_id': internal_type.id,
                    'location_id': rented_location.id,
                    'location_dest_id': stock_location.id,
                    'origin': order.name,
                    'partner_id': order.partner_id.id,
                }
                picking = self.env['stock.picking'].create(picking_vals)

                for line in order.order_line.filtered(lambda l: l.lot_id and l.lot_id.is_hifi_unit):
                    move_vals = {
                        'name': line.name,
                        'product_id': line.product_id.id,
                        'product_uom': line.product_uom.id,
                        'product_uom_qty': 1.0,
                        'location_id': rented_location.id,
                        'location_dest_id': stock_location.id,
                        'picking_id': picking.id,
                    }

                    if line.lot_id:
                        move_vals['lot_ids'] = [(4, line.lot_id.id)]

                    move = self.env['stock.move'].create(move_vals)
                    move._action_confirm()
                    move._action_assign()

                    if line.lot_id and move.move_line_ids:
                        move.move_line_ids.write({'lot_id': line.lot_id.id})

                if all(move.state == 'assigned' for move in picking.move_ids):
                    picking.button_validate()

            order.write({
                'rental_state': 'returned',
                'rental_return_date': fields.Date.today(),
            })

        return True

    @api.model
    def _cron_check_overdue_rentals(self):
        overdue = self.search([
            ('computed_order_type', '=', 'rental'),
            ('rental_state', '=', 'active'),
            ('rental_end_date', '<', fields.Date.today()),
        ])
        overdue.write({'rental_state': 'overdue'})

    def action_open_sale_unit_wizard(self):
        self.ensure_one()
        return {
            'name': _("Ajouter un appareil"),
            'type': 'ir.actions.act_window',
            'res_model': 'sale.unit.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_sale_order_id': self.id},
        }


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    lot_id = fields.Many2one(
        'stock.lot',
        string="Numéro de série (Stock)",
        help="Numéro de série pour le suivi du stock",
        copy=False,
    )

    def _prepare_procurement_values(self, group_id=False):
        values = super()._prepare_procurement_values(group_id=group_id)
        if self.lot_id:
            values['lot_id'] = self.lot_id.id
        return values

    def _action_launch_stock_rule(self, previous_product_uom_qty=False):
        rental_lines = self.filtered(lambda l: l.order_id._is_rental())
        non_rental_lines = self - rental_lines

        if non_rental_lines:
            res = super(SaleOrderLine, non_rental_lines)._action_launch_stock_rule(
                previous_product_uom_qty=previous_product_uom_qty
            )
            for line in non_rental_lines:
                if line.lot_id and line.move_ids:
                    for move in line.move_ids:
                        for move_line in move.move_line_ids:
                            if not move_line.lot_id:
                                move_line.lot_id = line.lot_id
        else:
            res = True

        return res


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        res = super().button_validate()
        return res


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def action_login_atelier(self):
        self.ensure_one()
        dashboard_view = self.env.ref('repair_custom.view_atelier_dashboard_kanban', raise_if_not_found=False)
        return {
            'name': _("Tableau de bord - %s") % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'atelier.dashboard.tile',
            'view_mode': 'kanban',
            'view_id': dashboard_view.id if dashboard_view else False,
            'target': 'main',
            'context': {
                'atelier_employee_id': self.id,
                'create': False,
            }
        }
