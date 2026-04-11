from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    repair_service_tax_id = fields.Many2one(
        'account.tax',
        string="TVA Services (Réparations/Locations)",
        help="Taxe appliquée automatiquement sur toutes les lignes de réparations et de locations.",
        domain=[('type_tax_use', '=', 'sale'), ('amount_type', '=', 'percent')],
    )

    quote_reminder_delay_days = fields.Integer(
        string="Délai avant relance devis (jours)",
        default=5,
        help="Nombre de jours après l'envoi du devis avant qu'un rappel automatique soit envoyé au client.",
    )

    quote_escalation_delay_days = fields.Integer(
        string="Délai avant escalade devis (jours)",
        default=3,
        help="Nombre de jours après la relance (ou après un clic 'Contacté') avant qu'une activité d'escalade soit créée pour le manager.",
    )

    def get_values(self):
        res = super().get_values()
        param = self.env['ir.config_parameter'].sudo()
        tax_id = param.get_param('repair_custom.service_tax_id')
        res['repair_service_tax_id'] = int(tax_id) if tax_id else False
        res['quote_reminder_delay_days'] = int(
            param.get_param('repair_custom.quote_reminder_delay_days', 5)
        )
        res['quote_escalation_delay_days'] = int(
            param.get_param('repair_custom.quote_escalation_delay_days', 3)
        )
        return res

    def set_values(self):
        super().set_values()
        param = self.env['ir.config_parameter'].sudo()
        param.set_param(
            'repair_custom.service_tax_id',
            str(self.repair_service_tax_id.id) if self.repair_service_tax_id else '',
        )
        param.set_param(
            'repair_custom.quote_reminder_delay_days',
            str(self.quote_reminder_delay_days or 5),
        )
        param.set_param(
            'repair_custom.quote_escalation_delay_days',
            str(self.quote_escalation_delay_days or 3),
        )
