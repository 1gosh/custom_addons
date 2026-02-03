from odoo import api, Command, fields, models, _

class RepairTags(models.Model):
    _name = "repair.tags"
    _description = "Repair Tags"
    def _get_default_color(self): return randint(1, 11)
    name = fields.Char('Nom de la panne', required=True)
    color = fields.Integer(string='Color Index', default=_get_default_color)
    is_global = fields.Boolean(string="Global", default=False)
    category_ids = fields.Many2many('repair.device.category', string="Catégories spécifiques")
    _sql_constraints = [('name_uniq', 'unique (name)', "Ce nom de panne existe déjà !")]
    
    @api.onchange('is_global')
    def _onchange_is_global_clear_categories(self):
        if self.is_global: self.category_ids = False

    @api.model
    def name_create(self, name):
        clean_name = name.strip()
        existing_tag = self.search([('name', '=ilike', clean_name)], limit=1)
        if existing_tag:
            if not existing_tag.is_global:
                cats = self.env.context.get('default_category_ids') or [self.env.context.get('default_category_id')]
                if cats and cats[0]:
                    existing_tag.write({'category_ids': [(4, c) for c in cats]})
            return existing_tag.id, existing_tag.display_name
        return super(RepairTags, self).name_create(clean_name)
    
    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None, order=None):
        """
        Recherche intelligente :
        1. Supporte les mots dans le désordre.
        2. FILTRE PAR CATÉGORIE (Si le contexte le permet).
        """
        args = args or []
        domain = []

        # 1. FILTRE TEXTUEL (Mots clés)
        if name:
            search_terms = name.split()
            for term in search_terms:
                # On cherche le terme dans le nom
                domain += [('name', operator, term)]

        # 2. FILTRE CONTEXTUEL (Catégorie active)
        # On regarde si la vue appelante nous a envoyé l'ID de la catégorie en cours
        # (ex: depuis la fiche réparation)
        ctx_category_id = self.env.context.get('filter_category_id') or self.env.context.get('default_category_id')

        if ctx_category_id:
            # LOGIQUE : Montrer le tag SI...
            # - Il est marqué "Global" (Valable pour tout)
            # - OU il est explicitement lié à la catégorie en cours (ou ses parents si vous gérez la hiérarchie)
            # - OU il n'a aucune catégorie spécifique (Optionnel, selon votre rigueur)
            
            domain += ['|', ('is_global', '=', True), ('category_ids', 'in', ctx_category_id)]

        return self._search(domain + args, limit=limit, access_rights_uid=name_get_uid, order=order)