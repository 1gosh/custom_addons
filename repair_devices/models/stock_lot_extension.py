from odoo import models, fields, api, _


class StockLot(models.Model):
    _inherit = 'stock.lot'

    is_hifi_unit = fields.Boolean(
        "Appareil physique HiFi",
        compute="_compute_is_hifi_unit",
        store=True,
        index=True,
    )
    hifi_partner_id = fields.Many2one(
        'res.partner',
        string="Propriétaire",
        ondelete="set null",
    )
    hifi_image = fields.Image("Photo")
    hifi_notes = fields.Text("Notes")
    hifi_variant_id = fields.Many2one(
        'repair.device.variant',
        string="Variante",
    )

    @api.depends("product_id", "product_id.brand_id", "product_id.name",
                 "hifi_variant_id", "hifi_variant_id.name", "name", "is_hifi_unit")
    @api.depends_context('lot_display')
    def _compute_display_name(self):
        hifi = self.filtered('is_hifi_unit')
        non_hifi = self - hifi
        if non_hifi:
            super(StockLot, non_hifi)._compute_display_name()

        lot_display = self.env.context.get('lot_display', 'full')
        for rec in hifi:
            if lot_display == 'serial_only':
                rec.display_name = rec.name or ""
            else:
                tmpl = rec.product_id.product_tmpl_id
                device_name = tmpl.display_name or rec.product_id.name or ""
                if rec.hifi_variant_id:
                    device_name += f" ({rec.hifi_variant_id.name})"
                if lot_display == 'full' and rec.name:
                    device_name += f" – SN: {rec.name}"
                rec.display_name = device_name

    @api.model
    def _name_search(self, name, domain=None, operator='ilike', limit=None, order=None):
        """Search lots by serial number, product name, or brand."""
        domain = domain or []
        if name:
            lot_domain = [
                '|', '|',
                ('name', operator, name),
                ('product_id.name', operator, name),
                ('product_id.product_tmpl_id.brand_id.name', operator, name),
            ]
            return self._search(lot_domain + domain, limit=limit, order=order)
        return super()._name_search(name, domain=domain, operator=operator, limit=limit, order=order)

    @api.depends('product_id.product_tmpl_id.is_hifi_device')
    def _compute_is_hifi_unit(self):
        for rec in self:
            rec.is_hifi_unit = bool(
                rec.product_id and rec.product_id.product_tmpl_id.is_hifi_device
            )
