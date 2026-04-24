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

    @api.depends("name", "is_hifi_unit")
    def _compute_display_name(self):
        """HiFi lots always display their serial; a single label avoids
        context-dependent rendering (which leaks between widgets on the
        same form). Use `format_hifi_label()` below when a richer label
        is needed (reports, stock move names, sale line descriptions)."""
        hifi = self.filtered('is_hifi_unit')
        non_hifi = self - hifi
        if non_hifi:
            super(StockLot, non_hifi)._compute_display_name()
        for rec in hifi:
            rec.display_name = rec.name or ""

    def format_hifi_label(self, include_serial=True):
        """Render a HiFi lot as 'Brand Model (Variant) – SN: XXX'.

        include_serial=False → just the device label. Use this anywhere
        you need the full descriptive form without touching display_name.
        """
        self.ensure_one()
        if not self.is_hifi_unit:
            return self.display_name
        tmpl = self.product_id.product_tmpl_id
        label = tmpl.display_name or self.product_id.name or ""
        if self.hifi_variant_id:
            label = f"{label} ({self.hifi_variant_id.name})" if label else self.hifi_variant_id.name
        if include_serial and self.name:
            label = f"{label} – SN: {self.name}" if label else self.name
        return label

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
