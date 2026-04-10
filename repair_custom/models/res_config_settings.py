from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    repair_service_tax_id = fields.Many2one(
        'account.tax',
        string="TVA Services (Réparations/Locations)",
        help="Taxe appliquée automatiquement sur toutes les lignes de réparations et de locations.",
        domain=[('type_tax_use', '=', 'sale'), ('amount_type', '=', 'percent')],
    )

    def get_values(self):
        res = super().get_values()
        param = self.env['ir.config_parameter'].sudo()
        tax_id = param.get_param('repair_custom.service_tax_id')
        res['repair_service_tax_id'] = int(tax_id) if tax_id else False
        return res

    def set_values(self):
        super().set_values()
        self.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.service_tax_id',
            str(self.repair_service_tax_id.id) if self.repair_service_tax_id else '',
        )
