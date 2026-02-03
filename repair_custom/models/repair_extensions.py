from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError

class RepairDeviceUnit(models.Model):
    _inherit = 'repair.device.unit'
    repair_order_ids = fields.One2many('repair.order', 'unit_id', string="Réparations associées")
    repair_order_count = fields.Integer(string="Réparations", compute='_compute_repair_order_count')

    stock_state = fields.Selection(
        selection_add=[
            ('in_repair', 'En Réparation'),
            ('sold', 'Vendu'),
            ('rented', 'En Location'),
        ],
        ondelete={'in_repair': 'set default', 'sold': 'set default'},
    )

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
                if last_repair and last_repair[0].state in ['done']:
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

    order_type = fields.Selection([
        ('standard', 'Commande Standard'),
        ('repair_quote', 'Devis Réparation'),
        ('equipment_sale', 'Vente Équipement'),
        ('rental', 'Location'),
    ], string="Type de commande", default='standard', tracking=True)

    # --- Rental fields ---
    rental_start_date = fields.Date("Date début location")
    rental_end_date = fields.Date("Date fin location")
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

    def action_confirm(self):
        """Override to handle equipment sales and rentals."""
        for order in self:
            # Validate rental dates
            if order.order_type == 'rental':
                if not order.rental_start_date or not order.rental_end_date:
                    raise UserError(_("Veuillez définir les dates de location avant de confirmer."))
                if order.rental_end_date < order.rental_start_date:
                    raise UserError(_("La date de fin doit être postérieure à la date de début."))

        # Call parent to create pickings/moves
        res = super().action_confirm()

        # Update device units immediately (no waiting for picking validation)
        for order in self:
            units = order.order_line.mapped('device_unit_id').filtered(lambda u: u)

            if order.order_type == 'rental':
                # Mark as rented
                units.write({'stock_state': 'rented'})
                order.rental_state = 'active'

                # Convert outgoing delivery to internal transfer (Stock → Rented location)
                rented_location = self.env.ref('repair_custom.stock_location_rented', raise_if_not_found=False)
                if rented_location:
                    pickings = order.picking_ids.filtered(
                        lambda p: p.state not in ['done', 'cancel'] and p.picking_type_code == 'outgoing'
                    )
                    for picking in pickings:
                        # Get internal picking type
                        warehouse = picking.picking_type_id.warehouse_id
                        internal_type = warehouse.int_type_id if warehouse else None

                        if internal_type:
                            # Change to internal transfer
                            picking.write({'picking_type_id': internal_type.id})

                            # Update destination location to Rented
                            picking.location_dest_id = rented_location
                            for move in picking.move_ids:
                                move.location_dest_id = rented_location

            elif order.order_type == 'equipment_sale':
                # Mark as sold immediately
                units.write({
                    'stock_state': 'sold',
                    'partner_id': order.partner_id.id,
                })

            # Auto-validate pickings for both types
            if order.order_type in ['rental', 'equipment_sale']:
                pickings = order.picking_ids.filtered(
                    lambda p: p.state not in ['done', 'cancel']
                )
                for picking in pickings:
                    # Validate if all moves are ready
                    if all(move.state == 'assigned' for move in picking.move_ids):
                        picking.button_validate()

        return res

    def action_return_rental(self):
        """Mark rental as returned and restore stock with internal transfer."""
        for order in self:
            if order.order_type != 'rental':
                continue

            units = order.order_line.mapped('device_unit_id').filtered(lambda u: u)
            units.write({'stock_state': 'stock'})

            # Create internal transfer from Rented → Stock
            rented_location = self.env.ref('repair_custom.stock_location_rented', raise_if_not_found=False)
            warehouse = order.warehouse_id or self.env['stock.warehouse'].search([
                ('company_id', '=', order.company_id.id)
            ], limit=1)

            if rented_location and warehouse:
                stock_location = warehouse.lot_stock_id
                internal_type = warehouse.int_type_id

                # Create picking for return
                picking_vals = {
                    'picking_type_id': internal_type.id,
                    'location_id': rented_location.id,
                    'location_dest_id': stock_location.id,
                    'origin': order.name,
                    'partner_id': order.partner_id.id,
                }
                picking = self.env['stock.picking'].create(picking_vals)

                # Create moves for each rented device
                for line in order.order_line.filtered(lambda l: l.device_unit_id):
                    move_vals = {
                        'name': line.name,
                        'product_id': line.product_id.id,
                        'product_uom': line.product_uom.id,
                        'product_uom_qty': 1.0,
                        'location_id': rented_location.id,
                        'location_dest_id': stock_location.id,
                        'picking_id': picking.id,
                    }

                    # Link to stock.lot if available
                    if line.lot_id:
                        move_vals['lot_ids'] = [(4, line.lot_id.id)]

                    move = self.env['stock.move'].create(move_vals)
                    move._action_confirm()
                    move._action_assign()

                    # Set lot on move lines
                    if line.lot_id and move.move_line_ids:
                        move.move_line_ids.write({'lot_id': line.lot_id.id})

                # Auto-validate the return picking
                if all(move.state == 'assigned' for move in picking.move_ids):
                    picking.button_validate()

            order.write({
                'rental_state': 'returned',
                'rental_return_date': fields.Date.today(),
            })

        return True

    @api.model
    def _cron_check_overdue_rentals(self):
        """Daily cron to mark overdue rentals."""
        overdue = self.search([
            ('order_type', '=', 'rental'),
            ('rental_state', '=', 'active'),
            ('rental_end_date', '<', fields.Date.today()),
        ])
        overdue.write({'rental_state': 'overdue'})

    def action_open_sale_unit_wizard(self):
        """Open wizard to add a device unit to the sale order."""
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

    device_unit_id = fields.Many2one(
        'repair.device.unit',
        string="Appareil physique",
        ondelete='set null',
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string="Numéro de série (Stock)",
        help="Numéro de série pour le suivi du stock",
        copy=False,
    )

    def _prepare_procurement_values(self, group_id=False):
        """Override to pass lot_id to stock moves."""
        values = super()._prepare_procurement_values(group_id=group_id)
        if self.lot_id:
            values['lot_id'] = self.lot_id.id
        return values

    def _action_launch_stock_rule(self, previous_product_uom_qty=False):
        """Override to set lot_id on move lines after procurement."""
        res = super()._action_launch_stock_rule(previous_product_uom_qty=previous_product_uom_qty)

        # Link the lot to the stock move lines
        for line in self:
            if line.lot_id and line.move_ids:
                for move in line.move_ids:
                    # Set lot on move lines
                    for move_line in move.move_line_ids:
                        if not move_line.lot_id:
                            move_line.lot_id = line.lot_id

        return res


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        """Override simplified - state changes now happen at order confirmation."""
        res = super().button_validate()

        # Stock state is now managed centrally in SaleOrder.action_confirm()
        # This override kept for potential future extensions (logging, notifications, etc.)

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
