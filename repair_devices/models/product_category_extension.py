from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = 'product.category'

    short_name = fields.Char(string="Abréviation")
