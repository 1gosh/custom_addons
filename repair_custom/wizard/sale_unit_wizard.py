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
    serial_number = fields.Char("Numéro de série")

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

    def action_add_to_order(self):
        """Add the selected device unit as a sale order line."""
        self.ensure_one()

        if self.create_new_unit:
            if not self.device_id:
                raise UserError(_("Veuillez sélectionner un modèle d'appareil."))
            unit_vals = {
                'device_id': self.device_id.id,
                'serial_number': self.serial_number,
                'stock_state': 'stock',
            }
            if self.variant_id:
                unit_vals['variant_id'] = self.variant_id.id
            unit = self.env['repair.device.unit'].create(unit_vals)
        else:
            if not self.unit_id:
                raise UserError(_("Veuillez sélectionner un appareil en stock."))
            unit = self.unit_id

        # Ensure the device has a linked product
        if not self.device_id.product_tmpl_id:
            self.device_id._sync_product_template()

        product_tmpl = self.device_id.product_tmpl_id
        product = product_tmpl.product_variant_id

        if not product:
            raise UserError(_("Aucun produit lié à cet appareil. Veuillez réessayer."))

        self.env['sale.order.line'].create({
            'order_id': self.sale_order_id.id,
            'product_id': product.id,
            'name': unit.display_name,
            'product_uom_qty': 1,
            'price_unit': self.price_unit,
            'device_unit_id': unit.id,
        })

        return {'type': 'ir.actions.act_window_close'}
