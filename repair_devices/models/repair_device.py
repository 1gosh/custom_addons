from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from random import randint
import re

class RepairDevice(models.Model):
    _name = "repair.device"
    _description = "Modèle Hi-Fi"
    _order = "brand_id, name"

    name = fields.Char("Nom du modèle", required=True)
    brand_id = fields.Many2one(
        "repair.device.brand",
        string="Marque",
        required=True,
        ondelete="restrict"
    )
    category_id = fields.Many2one(
        "repair.device.category",
        string="Catégorie",
        required=True,
        ondelete="restrict"
    )
    production_year = fields.Char("Année de sortie")
    variant_ids = fields.Many2many(
        "repair.device.variant",
        "repair_device_variant_rel",  # nom de la table relationnelle
        "device_id",
        "variant_id",
        string="Variantes"
    )
    unit_count = fields.Integer("# Appareils physiques", compute="_compute_unit_count")
    product_tmpl_id = fields.Many2one(
        'product.template',
        string="Produit lié",
        ondelete='set null',
        copy=False,
    )

    def _compute_unit_count(self):
        for rec in self:
            rec.unit_count = self.env['repair.device.unit'].search_count([('device_id', '=', rec.id)])

    @api.depends("brand_id", "brand_id.name", "name")
    def _compute_display_name(self):
        for rec in self:
            brand = rec.brand_id.name or ''
            model = rec.name or ''
            rec.display_name = f"{brand} {model}".strip()

    display_name = fields.Char(
        "Nom complet", compute="_compute_display_name", store=True,
    )

    def action_view_units(self):
        """Ouvre la liste des unités liées à ce modèle."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Appareils physiques',
            'res_model': 'repair.device.unit',
            'view_mode': 'tree,form',
            'domain': [('device_id', '=', self.id)],
            'context': {'default_device_id': self.id},
        }

    def action_sync_products(self):
        """Manual action to sync products for selected devices."""
        self._sync_product_template()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Synchronisation terminée"),
                'message': _("%s produit(s) synchronisé(s)") % len(self),
                'type': 'success',
            }
        }

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None, order=None):
        """
        Version corrigée avec l'argument 'order'.
        Recherche "Google-like" : Marque ET Modèle, peu importe l'ordre.
        """
        args = args or []
        domain = []
        
        if name:
            search_terms = name.split()      
            for term in search_terms:
                domain += ['|', ('brand_id.name', operator, term), ('name', operator, term)]
    
        return self._search(domain + args, limit=limit, access_rights_uid=name_get_uid, order=order)

    def name_get(self):
        res = []
        for rec in self:
            name = rec.display_name
            res.append((rec.id, name))
        return res

    @api.model
    def default_get(self, fields_list):
        defaults = super(RepairDevice, self).default_get(fields_list)
        
        input_name = self.env.context.get('default_name') or self.env.context.get('default_display_name')

        if input_name and not defaults.get('brand_id'):
            
            # 1. Fonction de nettoyage (On ne garde que les lettres et chiffres)
            def clean_str(s):
                return re.sub(r'[^a-z0-9]', '', s.lower()) if s else ''

            input_clean = clean_str(input_name)
            
            # 2. On récupère les marques
            brands = self.env['repair.device.brand'].search([])
            
            # On trie par longueur de la version NETTOYÉE (le plus long d'abord)
            sorted_brands = sorted(brands, key=lambda b: len(clean_str(b.name)), reverse=True)

            for brand in sorted_brands:
                brand_clean = clean_str(brand.name)
                
                # Ex: brand_clean="bangolufsen" (11 chars) 
                # input_clean="bangolufsenbeogram3000"
                
                if brand_clean and input_clean.startswith(brand_clean):
                    
                    # MATCH TROUVÉ !
                    defaults['brand_id'] = brand.id
                    
                    # 3. L'Algorithme du "Curseur" pour extraire le modèle
                    # Comme la chaîne d'origine "Bang Olufsen" est différente de "Bang & Olufsen",
                    # on ne peut pas couper simplement par la longueur du nom.
                    
                    target_length = len(brand_clean) # Nombre de "vraies" lettres à passer (11)
                    current_count = 0
                    cut_index = 0

                    # On parcourt la chaîne d'origine caractère par caractère
                    for i, char in enumerate(input_name):
                        if char.isalnum(): # Si c'est une lettre ou un chiffre
                            current_count += 1
                        
                        if current_count == target_length:
                            cut_index = i + 1 # On a trouvé la fin de la marque
                            break
                    
                    # Le modèle, c'est tout ce qu'il y a après ce curseur
                    remainder = input_name[cut_index:].strip()
                    
                    # 4. Petit nettoyage cosmétique du début du modèle
                    # Si l'utilisateur a tapé "Bang Olufsen - Beogram", le remainder est "- Beogram"
                    # On vire les tirets ou points qui traînent au début
                    remainder = re.sub(r'^[^a-zA-Z0-9]+', '', remainder)
                    
                    defaults['name'] = remainder.upper()
                    
                    # Optionnel : On peut aussi vider le 'name' pour forcer le recalcul si vous avez un compute
                    # defaults['name'] = False 
                    
                    break # On arrête à la première marque trouvée

        return defaults

    def _sync_product_template(self):
        """Create or update the linked product.template."""
        for rec in self:
            # Ensure display_name is computed
            rec._compute_display_name()

            vals = {
                'name': rec.display_name or f"{rec.brand_id.name} {rec.name}",
                'type': 'product',
                'sale_ok': True,
                'purchase_ok': False,
                'tracking': 'serial',
            }
            if rec.product_tmpl_id:
                rec.product_tmpl_id.write(vals)
            else:
                product = self.env['product.template'].create(vals)
                rec.write({'product_tmpl_id': product.id})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_product_template()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals or 'brand_id' in vals:
            self._sync_product_template()
        return res

    _sql_constraints = [
        ("unique_brand_model", "unique(brand_id, name)", "Ce modèle existe déjà pour cette marque."),
    ]

# --- 1. MARQUES ---------------------------------------------------------

class RepairDeviceBrand(models.Model):
    _name = "repair.device.brand"
    _description = "Marque Hi-Fi"
    _order = "name"

    name = fields.Char("Nom", required=True)
    country = fields.Char("Pays d’origine")
    founded_year = fields.Char("Année de création")
    website = fields.Char("Site web officiel")
    wiki_url = fields.Char("Lien HiFi-Wiki")
    description = fields.Text("Description")
    logo = fields.Image("Logo")

    model_ids = fields.One2many("repair.device", "brand_id", string="Modèles")

    _sql_constraints = [
        ("unique_brand_name", "unique(name)", "Cette marque existe déjà."),
    ]


# --- 2. CATÉGORIES ------------------------------------------------

class RepairDeviceCategory(models.Model):
    _name = "repair.device.category"
    _description = "Catégorie d’appareil Hi-Fi"
    _parent_name = "parent_id"
    _parent_store = True
    _rec_name = "complete_name"
    _order = "complete_name"

    company_id = fields.Many2one(
        'res.company', 
        string='Company', 
        default=lambda self: self.env.company,
        help="Société à laquelle cette catégorie appartient."
    )
    name = fields.Char("Nom", required=True, translate=True, index='trigram')
    complete_name = fields.Char("Nom complet", compute="_compute_complete_name", store=True, recursive=True)
    parent_id = fields.Many2one("repair.device.category", string="Catégorie parente", index=True, ondelete="cascade")
    parent_path = fields.Char(index=True, unaccent=False)
    internal_code = fields.Char("Code Interne")
    child_ids = fields.One2many("repair.device.category", "parent_id", string="Sous-catégories")
    description = fields.Text("Description")
    icon = fields.Char("Icône FontAwesome", help="Optionnel, pour les vues Kanban")
    device_model_ids = fields.One2many("repair.device", "category_id", string="Modèles de cette catégorie")
    product_count = fields.Integer("# Appareils", compute="_compute_device_count", help="Nombre d’appareils dans cette catégorie (ne compte pas les sous-catégories).")

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None, order=None):
        """
        Permet de trouver "Sources numériques / CD / Lecteur" 
        en tapant simplement "lecteur cd" ou "cd lecteur".
        """
        args = args or []
        domain = []
        
        if name:
            # 1. On découpe les mots
            search_terms = name.split()
            
            for term in search_terms:
                # 2. Chaque mot doit se trouver quelque part dans le chemin complet
                # L'opérateur 'ilike' cherche n'importe où dans la chaine
                domain += [('complete_name', operator, term)]
        
        # On passe le domaine construit à la méthode de recherche standard
        return self._search(domain + args, limit=limit, access_rights_uid=name_get_uid, order=order)

    # --- Calculs et contraintes ---
    @api.depends("name", "parent_id.complete_name")
    def _compute_complete_name(self):
        for cat in self:
            if cat.parent_id:
                cat.complete_name = f"{cat.parent_id.complete_name} / {cat.name}"
            else:
                cat.complete_name = cat.name

    @api.depends("device_model_ids")
    def _compute_device_count(self):
        for cat in self:
            cat.product_count = len(cat.device_model_ids)

    @api.constrains("parent_id")
    def _check_category_recursion(self):
        if not self._check_recursion():
            raise ValidationError(_("Vous ne pouvez pas créer de hiérarchie récursive."))

    #--- Gestion du nom affiché ---#
    @api.depends_context("hierarchical_naming")
    def _compute_display_name(self):
        if self.env.context.get("hierarchical_naming", True):
            super()._compute_display_name()
        else:
            for record in self:
                record.display_name = record.name


class RepairDeviceVariant(models.Model):
    _name = "repair.device.variant"
    _description = "Variante d'un modèle d'appareil"
    _order = "name"

    name = fields.Char("Nom de la variante", required=True)
    color = fields.Integer("Couleur", default=lambda self: randint(1, 11))

    device_ids = fields.Many2many(
        "repair.device",
        "repair_device_variant_rel",  # même table relationnelle !
        "variant_id",
        "device_id",
        string="Modèles associés",
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec, vals in zip(records, vals_list):
            device_ctx = self.env.context.get('default_device_id')
            if device_ctx:
                device = self.env['repair.device'].browse(device_ctx)
                device.variant_ids = [(4, rec.id)]
        return records
    
    @api.model
    def name_create(self, name):
        """Création rapide (ex: saisie au vol) depuis un champ Many2one/Many2many."""
        variant = self.create({'name': name})
        return variant.id, variant.name


class RepairDeviceUnit(models.Model):
    _name = "repair.device.unit"
    _description = "Instance physique d'un appareil"

    device_id = fields.Many2one(
        "repair.device",
        string="Modèle d’appareil",
        ondelete="cascade"
    )
    variant_id = fields.Many2one(
        "repair.device.variant",
        string="Variante"
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Propriétaire",
        ondelete="set null",
    )
    serial_number = fields.Char("Numéro de série")
    notes = fields.Text("Notes")
    image = fields.Image("Photo")
    display_name = fields.Char(
        string="Nom complet",
        compute="_compute_display_name",
        store=True
    )
    is_admin = fields.Boolean(
        compute="_compute_is_admin",
        string="Administrateur",
        store=False
    )
    device_name = fields.Char(
        string="Appareil",
        compute="_compute_device_name",
        store=True
    )
    stock_state = fields.Selection([
        ('client', 'Propriété Client'),
        ('stock', 'En Stock'),
        ('rented', 'En Location'), # Pour plus tard
    ], string="Statut Stock", default='client', tracking=True)

    @api.depends("device_id.display_name", "variant_id.name")
    def _compute_device_name(self):
        for rec in self:
            if rec.device_id:
                name = rec.device_id.display_name
                if rec.variant_id:
                    name += f" ({rec.variant_id.name})"
                rec.device_name = name
            else:
                rec.device_name = _("Aucun modèle")

    @api.depends("device_id.display_name", "variant_id.name", "serial_number")
    def _compute_display_name(self):
        for rec in self:
            name = rec.device_id.display_name or ""
            if rec.variant_id:
                name += f" ({rec.variant_id.name})"
            if rec.serial_number:
                name += f" – SN: {rec.serial_number}"
            rec.display_name = name

    @api.onchange('device_id')
    def _onchange_device_id(self):
        if self.device_id:
            return {'domain': {'variant_id': [('id', 'in', self.device_id.variant_ids.ids)]}}
        else:
            return {'domain': {'variant_id': []}}

    @api.constrains('variant_id', 'device_id')
    def _check_variant_device_consistency(self):
        for rec in self:
            if rec.variant_id and rec.variant_id not in rec.device_id.variant_ids:
                raise ValidationError(_("La variante sélectionnée n’appartient pas à ce modèle."))

    def action_toggle_edit(self):
        self.ensure_one()
        ctx = dict(self.env.context or {})
        ctx['edit_admin'] = not bool(ctx.get('edit_admin'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
            'context': ctx,
            # Optionnel: remettre la vue en mode lecture explicitement
            'flags': {'mode': 'readonly'} if not ctx['edit_admin'] else {},
        }
    
    def _compute_is_admin(self):
        user = self.env.user
        for rec in self:
            rec.is_admin = user.has_group('repair_custom.group_repair_admin')

    _sql_constraints = [
        (
            'unique_device_serial',
            'unique(device_id, serial_number)',
            "Ce numéro de série est déjà enregistré pour ce modèle. Veuillez utiliser l'unité existante."
        )
    ]