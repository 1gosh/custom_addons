from odoo import api, Command, fields, models, _

class RepairNotesTemplate(models.Model):
    _name = 'repair.notes.template'
    _description = 'Gabarit de Notes'
    _order = 'name'
    name = fields.Char("Nom du Gabarit", required=True)
    template_content = fields.Text("Contenu du Gabarit")
    category_ids = fields.Many2many('repair.device.category', string="Catégories d'appareils")

class RepairTemplateSelector(models.TransientModel):
    _name = 'repair.template.selector'
    _description = "Assistant d'import de gabarit"

    repair_id = fields.Many2one('repair.order', required=True)
    category_id = fields.Many2one('repair.device.category', string="Catégorie Filtre")
    
    # On choisit le gabarit ici
    template_id = fields.Many2one('repair.notes.template', string="Choisir un modèle")
    
    # La liste des lignes à cocher/décocher
    line_ids = fields.One2many('repair.template.line', 'wizard_id', string="Lignes du gabarit")
    
    # Options
    mode = fields.Selection([
        ('add', 'Ajouter à la suite'),
        ('replace', 'Remplacer tout')
    ], string="Mode d'insertion", default='add', required=True)

    @api.onchange('template_id')
    def _onchange_template_id(self):
        """ Quand on change de gabarit, on remplit la liste des lignes """
        if not self.template_id or not self.template_id.template_content:
            self.line_ids = [(5, 0, 0)] # Vider la liste
            return

        lines = []
        # On découpe le texte par saut de ligne
        raw_lines = self.template_id.template_content.split('\n')
        
        for content in raw_lines:
            # On ignore les lignes vides pour ne pas polluer
            if content.strip():
                lines.append((0, 0, {
                    'is_selected': True, # Coché par défaut
                    'content': content.strip()
                }))
        
        self.line_ids = [(5, 0, 0)] + lines

    def action_confirm(self):
        self.ensure_one()
        
        # 1. On récupère uniquement les lignes cochées
        selected_lines = self.line_ids.filtered(lambda l: l.is_selected).mapped('content')
        
        if not selected_lines:
            return {'type': 'ir.actions.act_window_close'}

        # 2. On reconstruit le texte final
        text_to_insert = '\n'.join(selected_lines)
        
        # 3. On met à jour la réparation
        current_notes = self.repair_id.internal_notes or ""
        
        if self.mode == 'replace':
            final_text = text_to_insert
        else:
            # Si ajout, on gère proprement les sauts de ligne
            separator = "\n\n" if current_notes else ""
            final_text = f"{current_notes}{separator}{text_to_insert}"
            
        self.repair_id.internal_notes = final_text
        
        return {'type': 'ir.actions.act_window_close'}

class RepairTemplateLine(models.TransientModel):
    _name = 'repair.template.line'
    _description = "Ligne de gabarit"

    wizard_id = fields.Many2one('repair.template.selector')
    is_selected = fields.Boolean(string="Inclure", default=True)
    content = fields.Char(string="Texte")