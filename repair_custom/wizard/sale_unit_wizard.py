from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SaleUnitWizard(models.TransientModel):
    _name = 'sale.unit.wizard'
    _description = "Assistant d'ajout d'appareil à une commande"

    sale_order_id = fields.Many2one('sale.order', string="Commande", required=True)

    # Step 1: select device model
    category_id = fields.Many2one('repair.device.category', string="Catégorie")
    device_id = fields.Many2one('repair.device', string="Modèle d'appareil", required=True)

    # Step 2: select or create unit
    create_new_unit = fields.Boolean("Créer une nouvelle unité", default=False)
    unit_id = fields.Many2one(
        'repair.device.unit',
        string="Appareil en stock",
        domain="[('device_id', '=', device_id), ('stock_state', '=', 'stock')]",
    )
    variant_id = fields.Many2one('repair.device.variant', string="Variante")
    serial_number = fields.Char("Numéro de série", help="Obligatoire pour créer un nouveau stock")

    # Step 3: price
    price_unit = fields.Float("Prix de vente", required=True)

    @api.onchange('category_id')
    def _onchange_category_id(self):
        if self.device_id and self.category_id:
            if self.device_id.category_id != self.category_id:
                self.device_id = False

    @api.onchange('device_id')
    def _onchange_device_id(self):
        self.unit_id = False
        self.variant_id = False

    def _get_stock_location(self):
        """Get the main stock location (WH/Stock)."""
        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.sale_order_id.company_id.id)
        ], limit=1)
        if not warehouse:
            raise UserError(_("Aucun entrepôt trouvé pour cette société."))
        return warehouse.lot_stock_id

    def _create_stock_lot_and_quant(self, product, serial_number):
        """Create stock.lot and initial stock.quant for the product."""
        if not serial_number:
            raise UserError(_("Le numéro de série est obligatoire pour créer un stock."))

        # Create stock.lot (renamed from stock.production.lot in Odoo 17)
        lot = self.env['stock.lot'].create({
            'name': serial_number,
            'product_id': product.id,
            'company_id': self.sale_order_id.company_id.id,
        })

        # Create initial stock at WH/Stock location
        stock_location = self._get_stock_location()
        self.env['stock.quant']._update_available_quantity(
            product,
            stock_location,
            quantity=1.0,
            lot_id=lot,
        )

        return lot

    def action_add_to_order(self):
        """Add the selected device unit as a sale order line."""
        self.ensure_one()

        # Ensure the device has a linked product
        if not self.device_id.product_tmpl_id:
            self.device_id._sync_product_template()

        product_tmpl = self.device_id.product_tmpl_id
        product = product_tmpl.product_variant_id

        if not product:
            raise UserError(_("Aucun produit lié à cet appareil. Veuillez réessayer."))

        # Ensure product is tracked by serial number
        if product.tracking != 'serial':
            product.tracking = 'serial'

        stock_lot = None

        if self.create_new_unit:
            # Create new unit + stock.lot + stock.quant
            if not self.serial_number:
                raise UserError(_("Le numéro de série est obligatoire pour créer une nouvelle unité."))

            # Create stock.lot and initial inventory
            stock_lot = self._create_stock_lot_and_quant(product, self.serial_number)

            # Create repair.device.unit
            unit_vals = {
                'device_id': self.device_id.id,
                'serial_number': self.serial_number,
                'stock_state': 'stock',
            }
            if self.variant_id:
                unit_vals['variant_id'] = self.variant_id.id
            unit = self.env['repair.device.unit'].create(unit_vals)

        else:
            # Use existing unit
            if not self.unit_id:
                raise UserError(_("Veuillez sélectionner un appareil en stock."))
            unit = self.unit_id

            # Find or create the stock.lot for this unit
            if unit.serial_number:
                stock_lot = self.env['stock.lot'].search([
                    ('name', '=', unit.serial_number),
                    ('product_id', '=', product.id),
                ], limit=1)

                if not stock_lot:
                    # Create stock.lot and initial inventory for existing unit
                    stock_lot = self._create_stock_lot_and_quant(product, unit.serial_number)

        # Create sale order line
        line_vals = {
            'order_id': self.sale_order_id.id,
            'product_id': product.id,
            'name': unit.display_name,
            'product_uom_qty': 1,
            'price_unit': self.price_unit,
            'device_unit_id': unit.id,
        }

        # Link stock.lot if available (for proper stock tracking)
        # Note: lot_id on sale.order.line is used during delivery
        # but we'll handle this in the sale.order.line write method

        line = self.env['sale.order.line'].create(line_vals)

        # Store the lot_id in a custom field for later use during delivery
        if stock_lot:
            line.write({'lot_id': stock_lot.id})

        return {'type': 'ir.actions.act_window_close'}
