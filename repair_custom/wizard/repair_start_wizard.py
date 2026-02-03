from odoo import api, Command, fields, models, _

class RepairWarnQuoteWizard(models.TransientModel):
    _name = 'repair.warn.quote.wizard'
    _description = "Avertissement Devis"

    repair_id = fields.Many2one('repair.order')
    
    def action_force_terminate(self):
        self.ensure_one()
        
        # Log de tracabilité
        self.repair_id.message_post(body="⚠️ Clôture forcée (Devis non validé ignoré).")
        
        # MAGIE : On rappelle la méthode d'origine avec le contexte 'force_stop'
        # Cela va passer outre le 'if' bloquant et exécuter la création d'activité !
        return self.repair_id.with_context(force_stop=True).action_repair_done()

    def action_go_to_quote(self):
        self.ensure_one()
        return self.repair_id.action_atelier_request_quote()

class RepairStartWizard(models.TransientModel):
    _name = 'repair.start.wizard'
    _description = "Avertissement démarrage réparation"
    repair_id = fields.Many2one('repair.order', required=True)
    message = fields.Text(readonly=True, default="Un devis est exigé pour cette réparation. Vous pouvez faire la demande maintenant ou passer.")

    def action_force_start(self):
        self.ensure_one()
        return self.repair_id.with_context(force_start=True).action_atelier_start()

    def action_go_to_quote(self):
        """ Option 2 : On commence ET on demande le devis tout de suite """
        self.ensure_one()
        self.repair_id.with_context(force_start=True, start_with_quote=True).action_atelier_start()
        return self.repair_id.action_atelier_request_quote()