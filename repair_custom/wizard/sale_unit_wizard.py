from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SaleUnitWizard(models.TransientModel):
    _name = 'sale.unit.wizard'
    _description = "Assistant d'ajout d'appareil à une commande"

    sale_order_id = fields.Many2one('sale.order', string="Commande", required=True)

    # Step 1: select device model
    category_id = fields.Many2one('repair.device.category', string="Catégorie")
    product_tmpl_id = fields.Many2one('product.template', string="Modèle d'appareil", required=True,
                                       domain=[('is_hifi_device', '=', True)])

    # Step 2: select or create unit
    has_available_lots = fields.Boolean(
        compute='_compute_has_available_lots',
        string="Has Available Units"
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string="Appareil en stock",
        domain="[('product_id.product_tmpl_id', '=', product_tmpl_id), ('stock_state', '=', 'stock'), ('is_hifi_unit', '=', True)]",
    )
    variant_id = fields.Many2one('repair.device.variant', string="Variante")
    serial_number = fields.Char("Numéro de série", help="Entrez un numéro de série pour créer une nouvelle unité, ou sélectionnez un appareil existant ci-dessus")

    # Step 3: price
    price_unit = fields.Monetary("Prix de vente", required=True, currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    @api.depends('product_tmpl_id')
    def _compute_has_available_lots(self):
        for wizard in self:
            if wizard.product_tmpl_id:
                product = wizard.product_tmpl_id.product_variant_id
                if product:
                    count = self.env['stock.lot'].search_count([
                        ('product_id', '=', product.id),
                        ('stock_state', '=', 'stock'),
                        ('is_hifi_unit', '=', True),
                    ])
                    wizard.has_available_lots = count > 0
                else:
                    wizard.has_available_lots = False
            else:
                wizard.has_available_lots = False

    @api.onchange('category_id')
    def _onchange_category_id(self):
        if self.product_tmpl_id and self.category_id:
            if self.product_tmpl_id.hifi_category_id != self.category_id:
                self.product_tmpl_id = False

    @api.onchange('product_tmpl_id')
    def _onchange_product_tmpl_id(self):
        self.lot_id = False
        self.variant_id = False
        self.serial_number = False

    @api.onchange('lot_id')
    def _onchange_lot_id(self):
        if self.lot_id:
            self.serial_number = False
            self.variant_id = False

    def _get_stock_location(self):
        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.sale_order_id.company_id.id)
        ], limit=1)
        if not warehouse:
            raise UserError(_("Aucun entrepôt trouvé pour cette société."))
        return warehouse.lot_stock_id

    def _create_stock_lot_and_quant(self, product, serial_number):
        if not serial_number:
            raise UserError(_("Le numéro de série est obligatoire pour créer un stock."))

        lot = self.env['stock.lot'].create({
            'name': serial_number,
            'product_id': product.id,
            'company_id': self.sale_order_id.company_id.id,
            'is_hifi_unit': True,
        })

        stock_location = self._get_stock_location()
        self.env['stock.quant']._update_available_quantity(
            product,
            stock_location,
            quantity=1.0,
            lot_id=lot,
        )

        return lot

    def action_add_to_order(self):
        self.ensure_one()

        product_tmpl = self.product_tmpl_id
        product = product_tmpl.product_variant_id

        if not product:
            raise UserError(_("Aucun produit lié à cet appareil. Veuillez réessayer."))

        # Ensure product is tracked by serial number
        if product.tracking != 'serial':
            product.tracking = 'serial'

        stock_lot = None

        if self.lot_id:
            # Use existing lot
            stock_lot = self.lot_id

        elif self.serial_number:
            # Create new lot + stock.quant
            stock_lot = self._create_stock_lot_and_quant(product, self.serial_number)

            # Set variant on the new lot
            if self.variant_id:
                stock_lot.hifi_variant_id = self.variant_id

        else:
            raise UserError(_("Veuillez sélectionner un appareil en stock ou entrer un numéro de série."))

        # Create sale order line
        line_vals = {
            'order_id': self.sale_order_id.id,
            'product_id': product.id,
            'name': stock_lot.display_name,
            'product_uom_qty': 1,
            'price_unit': self.price_unit,
        }

        line = self.env['sale.order.line'].create(line_vals)

        # Link stock.lot
        if stock_lot:
            line.write({'lot_id': stock_lot.id})

        return {'type': 'ir.actions.act_window_close'}
