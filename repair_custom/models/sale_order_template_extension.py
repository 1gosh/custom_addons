# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrderTemplate(models.Model):
    _inherit = 'sale.order.template'

    template_type = fields.Selection([
        ('standard', 'Commande Standard'),
        ('repair_quote', 'Devis Réparation'),
        ('equipment_sale', 'Vente Équipement'),
        ('rental', 'Location'),
    ], string="Type de modèle", default='standard', required=True,
       help="Définit le comportement de ce modèle de commande")
