from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from random import randint


# --- 1. MARQUES ---------------------------------------------------------

class RepairDeviceBrand(models.Model):
    _name = "repair.device.brand"
    _description = "Marque Hi-Fi"
    _order = "name"

    name = fields.Char("Nom", required=True)
    country = fields.Char("Pays d'origine")
    founded_year = fields.Char("Année de création")
    website = fields.Char("Site web officiel")
    wiki_url = fields.Char("Lien HiFi-Wiki")
    description = fields.Text("Description")
    logo = fields.Image("Logo")

    model_ids = fields.One2many("product.template", "brand_id", string="Modèles",
                                domain=[('is_hifi_device', '=', True)])

    _sql_constraints = [
        ("unique_brand_name", "unique(name)", "Cette marque existe déjà."),
    ]


# --- 2. CATÉGORIES ------------------------------------------------

class RepairDeviceCategory(models.Model):
    _name = "repair.device.category"
    _description = "Catégorie d'appareil Hi-Fi"
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
    device_model_ids = fields.One2many("product.template", "hifi_category_id", string="Modèles de cette catégorie")
    product_count = fields.Integer("# Appareils", compute="_compute_device_count", help="Nombre d'appareils dans cette catégorie (ne compte pas les sous-catégories).")

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None, order=None):
        args = args or []
        domain = []
        if name:
            search_terms = name.split()
            for term in search_terms:
                domain += [('complete_name', operator, term)]
        return self._search(domain + args, limit=limit, access_rights_uid=name_get_uid, order=order)

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
        "product.template",
        "product_template_variant_rel",
        "repair_device_variant_id",
        "product_template_id",
        string="Modèles associés",
        domain=[('is_hifi_device', '=', True)],
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec, vals in zip(records, vals_list):
            device_ctx = self.env.context.get('default_product_tmpl_id')
            if device_ctx:
                device = self.env['product.template'].browse(device_ctx)
                device.hifi_variant_ids = [(4, rec.id)]
        return records

    @api.model
    def name_create(self, name):
        variant = self.create({'name': name})
        return variant.id, variant.name
