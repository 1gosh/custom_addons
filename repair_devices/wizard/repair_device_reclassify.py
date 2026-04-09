from odoo import models, fields, api, _


class RepairDeviceReclassify(models.TransientModel):
    _name = "repair.device.reclassify"
    _description = "Réassignation de masse des appareils"

    # Les appareils sélectionnés (rempli automatiquement par le contexte)
    device_ids = fields.Many2many('product.template', string="Appareils à déplacer")

    # La cible
    new_category_id = fields.Many2one(
        'product.category',
        string="Nouvelle Catégorie",
        required=True,
    )

    # Optionnel : changer aussi la marque en masse si besoin
    new_brand_id = fields.Many2one(
        'repair.device.brand',
        string="Nouvelle Marque",
        help="Laisser vide pour conserver la marque actuelle"
    )

    @api.model
    def default_get(self, fields):
        res = super(RepairDeviceReclassify, self).default_get(fields)
        active_ids = self.env.context.get('active_ids')
        if active_ids and self.env.context.get('active_model') == 'product.template':
            res['device_ids'] = [(6, 0, active_ids)]
        return res

    def action_apply(self):
        self.ensure_one()
        vals = {'categ_id': self.new_category_id.id}

        if self.new_brand_id:
            vals['brand_id'] = self.new_brand_id.id

        self.device_ids.write(vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Succès"),
                'message': _("%s appareils ont été déplacés vers %s") % (len(self.device_ids), self.new_category_id.display_name),
                'type': 'success',
                'sticky': False,
            }
        }
