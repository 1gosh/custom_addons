from odoo import api, fields, models, _
from odoo.exceptions import UserError


class HifiInventoryWizard(models.TransientModel):
    _name = 'hifi.inventory.wizard'
    _description = "Assistant d'inventaire HiFi"

    product_tmpl_id = fields.Many2one(
        'product.template', string="Appareil", required=True,
        domain=[('is_hifi_device', '=', True)],
    )
    serial_number = fields.Char(string="Numéro de série", required=True)
    location_id = fields.Many2one(
        'stock.location', string="Emplacement",
        domain=[('usage', '=', 'internal')],
    )
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('done', 'Fait'),
    ], string="État", default='draft', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'location_id' in fields_list and not res.get('location_id'):
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.env.company.id)
            ], limit=1)
            if warehouse:
                res['location_id'] = warehouse.lot_stock_id.id
        return res

    def action_apply(self):
        """Process a single inventory line: create lot + quant."""
        for rec in self:
            if rec.state == 'done':
                continue

            product = rec.product_tmpl_id.product_variant_id
            if not product:
                raise UserError(_(
                    "Aucune variante trouvée pour le produit '%s'."
                ) % rec.product_tmpl_id.display_name)

            # Check serial uniqueness
            existing = self.env['stock.lot'].search([
                ('name', '=', rec.serial_number),
                ('product_id', '=', product.id),
            ], limit=1)
            if existing:
                raise UserError(_(
                    "Le numéro de série '%s' existe déjà pour le produit '%s'."
                ) % (rec.serial_number, rec.product_tmpl_id.display_name))

            lot = self.env['stock.lot'].create({
                'name': rec.serial_number,
                'product_id': product.id,
                'company_id': self.env.company.id,
            })

            self.env['stock.quant']._update_available_quantity(
                product, rec.location_id, 1.0, lot_id=lot,
            )

            rec.state = 'done'

    def action_apply_all(self):
        """Process all draft lines for the current user."""
        drafts = self.search([
            ('state', '=', 'draft'),
            ('create_uid', '=', self.env.uid),
        ])
        drafts.action_apply()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hifi.inventory.wizard',
            'view_mode': 'tree',
            'target': 'current',
        }

    def action_clear_all(self):
        """Delete all inventory lines for the current user."""
        records = self.search([
            ('create_uid', '=', self.env.uid),
        ])
        records.unlink()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hifi.inventory.wizard',
            'view_mode': 'tree',
            'target': 'current',
        }
