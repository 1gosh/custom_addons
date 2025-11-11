from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from random import randint

class RepairDevice(models.Model):
    _name = "repair.device"
    _description = "Modèle Hi-Fi"
    _order = "brand_id, name"

    name = fields.Char("Nom du modèle", required=True)
    brand_id = fields.Many2one(
        "repair.device.brand",
        string="Marque",
        required=True,
        ondelete="restrict",
    )
    category_id = fields.Many2one(
        "repair.device.category",
        string="Catégorie",
        ondelete="set null"
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

    def _compute_unit_count(self):
        for rec in self:
            rec.unit_count = self.env['repair.device.unit'].search_count([('device_id', '=', rec.id)])

    @api.depends("brand_id", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.brand_id.name or ''} {rec.name or ''}".strip()

    display_name = fields.Char(
        "Nom complet", compute="_compute_display_name", store=True,
    )
    # --- SMART BUTTON ---
    unit_count = fields.Integer(
        string="Appareils physiques",
        compute='_compute_unit_count',
        store=False,
    )

    def _compute_unit_count(self):
        for rec in self:
            rec.unit_count = self.env['repair.device.unit'].search_count([
                ('device_id', '=', rec.id)
            ])

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

    # Saisie intelligente : recherche sur brand + model
    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=80):
        args = args or []
        domain = []
        if name:
            domain = [
                "|",
                ("name", operator, name),
                ("brand_id.name", operator, name),
            ]
        models = self.search(domain + args, limit=limit)
        return models.name_get()

    def name_get(self):
        res = []
        for rec in self:
            name = rec.display_name
            res.append((rec.id, name))
        return res

    _sql_constraints = [
        ("unique_brand_model", "unique(brand_id, name)", "Ce modèle existe déjà pour cette marque."),
    ]   


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
        required=True,
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
    state = fields.Selection(
        [
            ('new', 'Neuf'),
            ('tb', 'Très bon état'),
            ('good', 'Bon état'),
            ('bad', 'Mauvais état')
        ],
        string="État",
        default='good',
        required=True,
    )
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
        store=False
    )

    @api.depends("device_id", "variant_id")
    def _compute_device_name(self):
        for rec in self:
            if rec.device_id:
                name = rec.device_id.display_name
                if rec.variant_id:
                    name += f" ({rec.variant_id.name})"
                rec.device_name = name
            else:
                rec.device_name = _("Aucun modèle")

    def _compute_is_admin(self):
        user = self.env.user
        for rec in self:
            rec.is_admin = user.has_group('rapair_custom.group_repair_admin')

    @api.depends("device_id", "variant_id", "serial_number")
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
