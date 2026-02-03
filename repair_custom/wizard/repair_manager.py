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

        # Access control validation
        is_manager = self.env.user.has_group('repair_custom.group_repair_manager')
        is_admin = self.env.user.has_group('repair_custom.group_repair_admin')

        if not (is_manager or is_admin):
            raise UserError(_("Vous n'avez pas les permissions nécessaires pour effectuer cette opération."))

        # Validate that repairs are in modifiable state
        non_modifiable = self.repair_ids.filtered(lambda r: r.state in ('cancel', 'delivered'))
        if non_modifiable:
            raise UserError(_(
                "Certaines réparations ne peuvent pas être modifiées (annulées ou livrées): %s"
            ) % ', '.join(non_modifiable.mapped('name')))

        # Validate user can modify selected repairs
        # Technicians can only modify repairs assigned to them or unassigned
        if not is_manager and not is_admin:
            current_employee = self.env.user.employee_id
            unauthorized = self.repair_ids.filtered(
                lambda r: r.technician_employee_id and r.technician_employee_id != current_employee
            )
            if unauthorized:
                raise UserError(_(
                    "Vous ne pouvez modifier que vos propres réparations ou les réparations non assignées. "
                    "Réparations non autorisées: %s"
                ) % ', '.join(unauthorized.mapped('name')))

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
                # Batch operation: Use SQL for better performance with many records
                repair_ids = self.repair_ids.ids
                tag_ids = self.new_tag_ids.ids

                # Insert tag relationships in bulk (avoid duplicates with ON CONFLICT)
                query = """
                    INSERT INTO repair_order_repair_tags_rel (repair_order_id, repair_tags_id)
                    SELECT repair_id, tag_id
                    FROM unnest(%s::int[]) AS repair_id
                    CROSS JOIN unnest(%s::int[]) AS tag_id
                    ON CONFLICT DO NOTHING
                """
                self.env.cr.execute(query, (repair_ids, tag_ids))

            # Cas C : RETIRER (Enlever spécifiquement ces tags)
            elif self.tag_action == 'remove':
                # Batch operation: Delete tag relationships in bulk
                repair_ids = self.repair_ids.ids
                tag_ids = self.new_tag_ids.ids

                query = """
                    DELETE FROM repair_order_repair_tags_rel
                    WHERE repair_order_id IN %s AND repair_tags_id IN %s
                """
                self.env.cr.execute(query, (tuple(repair_ids), tuple(tag_ids)))

        # Audit logging: track mass changes
        changes_made = []
        if self.update_technician:
            changes_made.append(f"Technicien → {self.new_technician_id.name}")
        if self.update_priority:
            priority_label = dict(self._fields['new_priority'].selection).get(self.new_priority)
            changes_made.append(f"Priorité → {priority_label}")
        if self.update_warranty:
            warranty_label = dict(self._fields['new_warranty'].selection).get(self.new_warranty)
            changes_made.append(f"Garantie → {warranty_label}")
        if self.update_tags:
            tag_names = ', '.join(self.new_tag_ids.mapped('name'))
            changes_made.append(f"Tags ({self.tag_action}) → {tag_names}")

        # Post message to each repair for audit trail
        if changes_made:
            message = "Mise à jour en masse par %s:\n%s" % (
                self.env.user.name,
                '\n'.join(f"• {change}" for change in changes_made)
            )
            for repair in self.repair_ids:
                repair.message_post(body=message, subtype_xmlid='mail.mt_note')

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