from odoo import api, fields, models, _
from odoo.exceptions import UserError


class DeviceStockWizard(models.TransientModel):
    _name = 'device.stock.wizard'
    _description = "Assistant d'entrée en stock d'un appareil"

    lot_id = fields.Many2one(
        'stock.lot', string="Appareil", required=True,
        readonly=True,
        domain=[('is_hifi_unit', '=', True)],
    )
    repair_id = fields.Many2one(
        'repair.order', string="Réparation",
        readonly=True,
    )
    is_abandon = fields.Boolean(
        string="Mode Abandon", compute='_compute_is_abandon',
    )
    location_dest_id = fields.Many2one(
        'stock.location', string="Emplacement de destination",
        required=True,
        domain="[('usage', '=', 'internal')]",
    )
    previous_owner_id = fields.Many2one(
        related='lot_id.hifi_partner_id', string="Propriétaire actuel",
        readonly=True,
    )
    current_stock_state = fields.Selection(
        related='lot_id.stock_state', string="Statut stock actuel",
        readonly=True,
    )
    note = fields.Text(string="Note")

    @api.depends('repair_id')
    def _compute_is_abandon(self):
        for wiz in self:
            wiz.is_abandon = bool(wiz.repair_id)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        if 'location_dest_id' in fields_list:
            repair_id = res.get('repair_id')
            lot_id = res.get('lot_id')

            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.env.company.id)
            ], limit=1)
            collection = self.env.ref(
                'repair_custom.stock_location_collection',
                raise_if_not_found=False,
            )

            if repair_id:
                repair = self.env['repair.order'].browse(repair_id)
                if repair.exists() and lot_id:
                    lot = self.env['stock.lot'].browse(lot_id)

                    if repair.state == 'done' and lot.functional_state == 'working':
                        if warehouse:
                            res['location_dest_id'] = warehouse.lot_stock_id.id
                    elif repair.state == 'irreparable':
                        if collection:
                            res['location_dest_id'] = collection.id
                    elif repair.state == 'under_repair' and lot.functional_state == 'working':
                        if warehouse:
                            res['location_dest_id'] = warehouse.lot_stock_id.id
                    elif repair.state in ('draft', 'confirmed'):
                        if collection:
                            res['location_dest_id'] = collection.id
                    else:
                        if collection:
                            res['location_dest_id'] = collection.id

            elif lot_id:
                lot = self.env['stock.lot'].browse(lot_id)
                if lot.exists():
                    if lot.functional_state == 'working':
                        if warehouse:
                            res['location_dest_id'] = warehouse.lot_stock_id.id
                    else:
                        if collection:
                            res['location_dest_id'] = collection.id

        return res

    def action_confirm(self):
        """Create stock operations and update lot/repair records."""
        self.ensure_one()

        lot = self.lot_id
        product = lot.product_id

        if not product:
            raise UserError(_("Aucun produit lié à cet appareil."))

        # Ensure product is tracked by serial number
        if product.tracking != 'serial':
            product.tracking = 'serial'

        # Create stock.picking + stock.move (receipt from customer)
        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.env.company.id)
        ], limit=1)
        if not warehouse:
            raise UserError(_("Aucun entrepôt trouvé pour cette société."))

        source_location = self.env.ref('stock.stock_location_customers')
        dest_location = self.location_dest_id
        picking_type = warehouse.in_type_id

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'origin': self._get_picking_origin(),
            'partner_id': lot.hifi_partner_id.id if lot.hifi_partner_id else False,
        })

        move = self.env['stock.move'].create({
            'name': lot.display_name,
            'product_id': product.id,
            'product_uom': product.uom_id.id,
            'product_uom_qty': 1.0,
            'location_id': source_location.id,
            'location_dest_id': dest_location.id,
            'picking_id': picking.id,
        })

        move._action_confirm()
        move._action_assign()

        # Set lot on move lines
        if move.move_line_ids:
            move.move_line_ids.write({'lot_id': lot.id, 'quantity': 1.0})
        else:
            self.env['stock.move.line'].create({
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': product.id,
                'product_uom_id': product.uom_id.id,
                'location_id': source_location.id,
                'location_dest_id': dest_location.id,
                'lot_id': lot.id,
                'quantity': 1.0,
            })

        # Validate picking
        picking.button_validate()

        # Update lot
        lot_vals = {
            'stock_state': 'stock',
        }
        if self.is_abandon:
            lot_vals['hifi_partner_id'] = self.env.company.partner_id.id
        lot.write(lot_vals)

        # Build and append tracking note
        self._append_tracking_note()

        # Handle abandon-specific logic
        if self.is_abandon:
            self._process_abandon()

        return {'type': 'ir.actions.act_window_close'}

    def _get_picking_origin(self):
        if self.is_abandon and self.repair_id:
            return _("Abandon %s") % self.repair_id.name
        return _("Entrée en stock %s") % self.lot_id.display_name

    def _append_tracking_note(self):
        date_str = fields.Date.today().strftime('%d/%m/%Y')
        location_name = self.location_dest_id.display_name

        if self.is_abandon and self.repair_id:
            partner_name = self.previous_owner_id.name if self.previous_owner_id else 'Inconnu'
            note_line = (
                "[%s] Appareil abandonné — Réparation: %s, "
                "Ancien propriétaire: %s. Emplacement: %s."
            ) % (date_str, self.repair_id.name, partner_name, location_name)
        else:
            note_line = "[%s] Entrée en stock — Emplacement: %s." % (
                date_str, location_name,
            )

        if self.note:
            note_line += " %s" % self.note

        existing_notes = self.lot_id.hifi_notes or ''
        if existing_notes:
            new_notes = existing_notes + '\n' + note_line
        else:
            new_notes = note_line

        self.lot_id.hifi_notes = new_notes

    def _process_abandon(self):
        repair = self.repair_id
        if not repair:
            return

        repair.delivery_state = 'abandoned'

        # Clear all warranty fields on the lot
        self.lot_id.write({
            'sale_date': False,
            'sav_expiry': False,
            'sale_order_id': False,
            'last_delivered_repair_id': False,
            'sar_expiry': False,
        })

        partner_name = self.previous_owner_id.name if self.previous_owner_id else 'Inconnu'
        location_name = self.location_dest_id.display_name
        body = _(
            "Appareil abandonné par %s. "
            "Mis en stock à l'emplacement: %s."
        ) % (partner_name, location_name)
        if self.note:
            body += "\n" + _("Note: %s") % self.note

        repair.message_post(body=body)
