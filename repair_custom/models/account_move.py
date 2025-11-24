# models/account_move.py
from odoo import models, fields, api

class AccountMove(models.Model):
    _inherit = 'account.move'

    # Lien vers la réparation
    repair_id = fields.Many2one(
        'repair.order', 
        string="Réparation d'origine",
        readonly=True,
        help="La réparation qui a généré cette facture."
    )

    # Champ technique pour afficher les notes dans la vue facture
    repair_notes = fields.Text(
        related='repair_id.internal_notes', 
        string="Notes de l'atelier", 
        readonly=True
    )