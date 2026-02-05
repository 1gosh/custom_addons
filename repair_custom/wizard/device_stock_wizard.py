from odoo import api, fields, models, _
from odoo.exceptions import UserError


class DeviceStockWizard(models.TransientModel):
    _name = 'device.stock.wizard'
    _description = "Assistant d'entrée en stock d'un appareil"

    unit_id = fields.Many2one(
        'repair.device.unit', string="Appareil", required=True,
        readonly=True,
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
        related='unit_id.partner_id', string="Propriétaire actuel",
        readonly=True,
    )
    current_stock_state = fields.Selection(
        related='unit_id.stock_state', string="Statut stock actuel",
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

        # Set default location based on context and repair/device state
        if 'location_dest_id' in fields_list:
            repair_id = res.get('repair_id')
            unit_id = res.get('unit_id')

            # Get warehouse and collection locations
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.env.company.id)
            ], limit=1)
            collection = self.env.ref(
                'repair_custom.stock_location_collection',
                raise_if_not_found=False,
            )

            if repair_id:
                # ABANDON MODE: Use repair state + functional state for smart defaults
                repair = self.env['repair.order'].browse(repair_id)
                if repair.exists() and unit_id:
                    unit = self.env['repair.device.unit'].browse(unit_id)

                    # Logic based on repair state
                    if repair.state == 'done' and unit.functional_state == 'working':
                        # Successfully repaired and working → Stock
                        if warehouse:
                            res['location_dest_id'] = warehouse.lot_stock_id.id
                    elif repair.state == 'irreparable':
                        # Device is irreparable → Collection
                        if collection:
                            res['location_dest_id'] = collection.id
                    elif repair.state == 'under_repair' and unit.functional_state == 'working':
                        # Work in progress but device is working → Stock
                        if warehouse:
                            res['location_dest_id'] = warehouse.lot_stock_id.id
                    elif repair.state in ('draft', 'confirmed'):
                        # Not yet worked on → Collection (needs assessment)
                        if collection:
                            res['location_dest_id'] = collection.id
                    else:
                        # Default case: broken or uncertain → Collection
                        if collection:
                            res['location_dest_id'] = collection.id

            elif unit_id:
                # GENERAL STOCK ENTRY MODE: Use functional state only
                unit = self.env['repair.device.unit'].browse(unit_id)
                if unit.exists():
                    if unit.functional_state == 'working':
                        # Working device → Stock
                        if warehouse:
                            res['location_dest_id'] = warehouse.lot_stock_id.id
                    else:
                        # Broken/fixing device → Collection
                        if collection:
                            res['location_dest_id'] = collection.id

        return res

    def _get_or_create_stock_lot(self):
        """Ensure device has a stock.lot linked. Create one if missing."""
        self.ensure_one()
        unit = self.unit_id
        device = unit.device_id

        if not device:
            raise UserError(_("Aucun modèle d'appareil lié à cette unité."))

        # Ensure device has a linked product
        if not device.product_tmpl_id:
            device._sync_product_template()

        product_tmpl = device.product_tmpl_id
        if not product_tmpl:
            raise UserError(_(
                "Impossible de créer un produit lié à l'appareil '%s'. "
                "Veuillez réessayer."
            ) % device.display_name)

        product = product_tmpl.product_variant_id
        if not product:
            raise UserError(_("Aucun produit trouvé pour cet appareil."))

        # Ensure product is tracked by serial number
        if product.tracking != 'serial':
            product.tracking = 'serial'

        serial = unit.serial_number
        if not serial:
            raise UserError(_(
                "L'appareil n'a pas de numéro de série. "
                "Veuillez en attribuer un avant de le mettre en stock."
            ))

        # Search existing lot
        lot = self.env['stock.lot'].search([
            ('name', '=', serial),
            ('product_id', '=', product.id),
        ], limit=1)

        if not lot:
            lot = self.env['stock.lot'].create({
                'name': serial,
                'product_id': product.id,
                'company_id': self.env.company.id,
            })

        return lot, product

    def action_confirm(self):
        """Create stock operations and update unit/repair records."""
        self.ensure_one()

        lot, product = self._get_or_create_stock_lot()

        # Save lot on unit
        self.unit_id.lot_id = lot

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
            'partner_id': self.unit_id.partner_id.id if self.unit_id.partner_id else False,
        })

        move = self.env['stock.move'].create({
            'name': self.unit_id.display_name,
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
            # Create move line manually if none auto-created
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

        # Update unit
        unit_vals = {
            'stock_state': 'stock',
            'lot_id': lot.id,
        }
        if self.is_abandon:
            unit_vals['partner_id'] = self.env.company.partner_id.id
        self.unit_id.write(unit_vals)

        # Build and append tracking note
        self._append_tracking_note()

        # Handle abandon-specific logic
        if self.is_abandon:
            self._process_abandon()

        return {'type': 'ir.actions.act_window_close'}

    def _get_picking_origin(self):
        """Build origin string for the picking."""
        if self.is_abandon and self.repair_id:
            return _("Abandon %s") % self.repair_id.name
        return _("Entrée en stock %s") % self.unit_id.display_name

    def _append_tracking_note(self):
        """Append a tracking note to the unit's notes field."""
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

        existing_notes = self.unit_id.notes or ''
        if existing_notes:
            new_notes = existing_notes + '\n' + note_line
        else:
            new_notes = note_line

        self.unit_id.notes = new_notes

    def _process_abandon(self):
        """Handle abandon-specific logic on the repair order."""
        repair = self.repair_id
        if not repair:
            return

        repair.delivery_state = 'abandoned'

        # Clear all warranty fields on the unit
        self.unit_id.write({
            'sale_date': False,
            'sav_expiry': False,
            'sale_order_id': False,
            'last_delivered_repair_id': False,
            'sar_expiry': False,
        })

        # Build message for chatter
        partner_name = self.previous_owner_id.name if self.previous_owner_id else 'Inconnu'
        location_name = self.location_dest_id.display_name
        body = _(
            "Appareil abandonné par %s. "
            "Mis en stock à l'emplacement: %s."
        ) % (partner_name, location_name)
        if self.note:
            body += "\n" + _("Note: %s") % self.note

        repair.message_post(body=body)
