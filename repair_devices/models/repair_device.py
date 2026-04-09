from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from random import randint


# --- 1. MARQUES ---------------------------------------------------------

class RepairDeviceBrand(models.Model):
    _name = "repair.device.brand"
    _description = "Marque Hi-Fi"
    _order = "name"

    name = fields.Char("Nom", required=True)
    country = fields.Char("Pays d'origine")
    founded_year = fields.Char("Année de création")
    website = fields.Char("Site web officiel")
    wiki_url = fields.Char("Lien HiFi-Wiki")
    description = fields.Text("Description")
    logo = fields.Image("Logo")

    model_ids = fields.One2many("product.template", "brand_id", string="Modèles",
                                domain=[('is_hifi_device', '=', True)])

    _sql_constraints = [
        ("unique_brand_name", "unique(name)", "Cette marque existe déjà."),
    ]


class RepairDeviceVariant(models.Model):
    _name = "repair.device.variant"
    _description = "Variante d'un modèle d'appareil"
    _order = "name"

    name = fields.Char("Nom de la variante", required=True)
    color = fields.Integer("Couleur", default=lambda self: randint(1, 11))

    device_ids = fields.Many2many(
        "product.template",
        "product_template_variant_rel",
        "repair_device_variant_id",
        "product_template_id",
        string="Modèles associés",
        domain=[('is_hifi_device', '=', True)],
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec, vals in zip(records, vals_list):
            device_ctx = self.env.context.get('default_product_tmpl_id')
            if device_ctx:
                device = self.env['product.template'].browse(device_ctx)
                device.hifi_variant_ids = [(4, rec.id)]
        return records

    @api.model
    def name_create(self, name):
        variant = self.create({'name': name})
        return variant.id, variant.name
