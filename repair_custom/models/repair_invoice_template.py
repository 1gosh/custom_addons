from odoo import models, fields, api

class RepairInvoiceTemplate(models.Model):
    _name = 'repair.invoice.template'
    _description = "Modèle de Facturation Atelier"

    name = fields.Char("Nom du Modèle", required=True, help="Ex: Restauration Ampli Vintage")
    
    # Lien avec les catégories d'articles pour le filtrage intelligent
    device_category_ids = fields.Many2many(
        'repair.device.category', 
        string="Catégories compatibles",
        help="Laissez vide pour rendre ce modèle disponible pour tous les appareils."
    )

    line_ids = fields.One2many('repair.invoice.template.line', 'template_id', string="Lignes de ventilation")
    active = fields.Boolean(default=True)

class RepairInvoiceTemplateLine(models.Model):
    _name = 'repair.invoice.template.line'
    _description = "Ligne de modèle de facture"
    _order = 'sequence'

    template_id = fields.Many2one('repair.invoice.template')
    sequence = fields.Integer(default=10)
    
    name = fields.Char("Libellé Facture", required=True, help="Le texte qui sera vu par le client")
    
    # Produit de type Service pour la compta (Compte 706)
    product_id = fields.Many2one(
        'product.product', 
        string="Article Service", 
        domain=[('type', '=', 'service')],
        required=True
    )
    
    weight_percentage = fields.Float("Pondération (%)", required=True, default=20.0)