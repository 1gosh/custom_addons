from odoo import models, fields, api, _

class RepairOrderMassUpdate(models.TransientModel):
    _name = "repair.manager"
    _description = "Gestionnaire de masse des Réparations"

    repair_ids = fields.Many2many('repair.order', string="Réparations ciblées")
    
    # --- 1. GESTION DES TAGS (PANNES) ---
    update_tags = fields.Boolean("Modifier les Pannes")
    tag_action = fields.Selection([
        ('add', 'Ajouter aux existants'),
        ('replace', 'Remplacer (Écraser tout)'),
        ('remove', 'Retirer ces pannes')
    ], string="Action Pannes", default='add')
    
    new_tag_ids = fields.Many2many('repair.tags', string="Pannes à appliquer")

    # --- 2. GESTION TECHNICIEN ---
    update_technician = fields.Boolean("Réassigner Technicien")
    new_technician_id = fields.Many2one('hr.employee', string="Nouveau Technicien")

    # --- 3. PRIORITÉ ---
    update_priority = fields.Boolean("Changer Priorité")
    new_priority = fields.Selection([('0', 'Normal'), ('1', 'Urgent')], default='1', string="Nouvelle Priorité")

    # --- 4. GARANTIE ---
    update_warranty = fields.Boolean("Changer Garantie")
    new_warranty = fields.Selection([
        ('aucune', 'Aucune'), 
        ('sav', 'SAV'), 
        ('sar', 'SAR')
    ], string="Nouvelle Garantie", default='sav')
    repair_count = fields.Integer(string="Nombre", compute='_compute_repair_count')

    @api.depends('repair_ids')
    def _compute_repair_count(self):
        for rec in self:
            rec.repair_count = len(rec.repair_ids)

    @api.model
    def default_get(self, fields):
        res = super(RepairOrderMassUpdate, self).default_get(fields)
        active_ids = self.env.context.get('active_ids')
        # On charge les enregistrements sélectionnés dans la liste
        if active_ids and self.env.context.get('active_model') == 'repair.order':
            res['repair_ids'] = [(6, 0, active_ids)]
        return res

    def action_apply(self):
        self.ensure_one()
        
        # Dictionnaire pour les champs simples (write direct)
        vals = {}
        
        # 1. Technicien
        if self.update_technician:
            vals['technician_employee_id'] = self.new_technician_id.id
            # On met à jour l'utilisateur lié aussi si possible (optionnel)
            if self.new_technician_id.user_id:
                vals['technician_user_id'] = self.new_technician_id.user_id.id

        # 2. Priorité
        if self.update_priority:
            vals['priority'] = self.new_priority

        # 3. Garantie
        if self.update_warranty:
            vals['repair_warranty'] = self.new_warranty

        # --- APPLICATION DES CHAMPS SIMPLES ---
        if vals:
            self.repair_ids.write(vals)

        # --- 4. TRAITEMENT COMPLEXE DES TAGS ---
        if self.update_tags and self.new_tag_ids:
            
            # Cas A : REMPLACER (Le plus simple, on écrase tout)
            if self.tag_action == 'replace':
                self.repair_ids.write({'tag_ids': [(6, 0, self.new_tag_ids.ids)]})
            
            # Cas B : AJOUTER (Sans toucher aux existants)
            elif self.tag_action == 'add':
                for repair in self.repair_ids:
                    # (4, id) = Link
                    repair.write({'tag_ids': [(4, tag.id) for tag in self.new_tag_ids]})

            # Cas C : RETIRER (Enlever spécifiquement ces tags)
            elif self.tag_action == 'remove':
                for repair in self.repair_ids:
                    # (3, id) = Unlink
                    repair.write({'tag_ids': [(3, tag.id) for tag in self.new_tag_ids]})

        # --- NOTIFICATION DE SUCCÈS ---
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Mise à jour terminée"),
                'message': _("%s réparations ont été mises à jour avec succès.") % len(self.repair_ids),
                'type': 'success',
                'sticky': False,
            }
        }