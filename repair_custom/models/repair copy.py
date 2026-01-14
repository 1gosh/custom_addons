class RepairQuotationWizard(models.TransientModel):
    _name = 'repair.quotation.wizard'
    _description = "Assistant Demande de Devis"
    repair_id = fields.Many2one('repair.order', string="Réparation", required=True)
    category_id = fields.Many2one('repair.device.category', string="Catégorie Appareil")
    quotation_notes = fields.Text(string="Estimation Technique", required=False)
    notes_template_id = fields.Many2one('repair.notes.template', string="Insérer un Gabarit", store=False)
    
    @api.onchange('notes_template_id')
    def _onchange_notes_template_id(self):
        if self.notes_template_id and self.notes_template_id.template_content:
            new_content = self.notes_template_id.template_content
            if self.quotation_notes:
                self.quotation_notes += '\n\n---\n\n' + new_content
            else:
                self.quotation_notes = new_content
            self.notes_template_id = False

    def action_confirm_request(self):
        self.ensure_one()

        self._assign_technician_if_needed()

        if not self.quotation_notes:
            raise UserError("Pour une demande de devis, vous devez remplir l'estimation technique.")
        
        group_manager = self.env.ref('repair_custom.group_repair_manager')
        for manager_user in group_manager.users:
            # On met l'activité sur la REPARATION (repair_id)
            self.repair_id.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=manager_user.id,
                summary="Validation Devis Requise", 
                note=f"Demande par {self.env.user.name} pour {self.repair_id.device_id_name}",
                date_deadline=fields.Date.today(),
            )
        self.repair_id.write({'quotation_notes': self.quotation_notes, 'state': 'quotation_pending', 'quote_required': True})
        return {'type': 'ir.actions.act_window_close'}

    def action_force_start(self):
        self.ensure_one()
        return self.repair_id.with_context(force_start=True, atelier_employee_id=self._context.get('atelier_employee_id')).action_atelier_start()