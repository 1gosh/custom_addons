from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import re


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_hifi_device = fields.Boolean(
        "Appareil HiFi",
        compute="_compute_is_hifi_device",
        store=True,
        index=True,
    )
    brand_id = fields.Many2one(
        'repair.device.brand',
        string="Marque",
        ondelete="restrict",
    )
    hifi_variant_ids = fields.Many2many(
        'repair.device.variant',
        relation="product_template_variant_rel",
        column1="product_template_id",
        column2="repair_device_variant_id",
        string="Variantes",
    )
    production_year = fields.Char("Année de sortie")
    hifi_unit_count = fields.Integer(
        "# Appareils physiques",
        compute="_compute_hifi_unit_count",
    )

    @api.depends('categ_id', 'categ_id.parent_path')
    def _compute_is_hifi_device(self):
        hifi_cat = self.env.ref('repair_devices.product_category_hifi', raise_if_not_found=False)
        for rec in self:
            if not hifi_cat or not rec.categ_id:
                rec.is_hifi_device = False
            else:
                rec.is_hifi_device = bool(
                    rec.categ_id.parent_path
                    and hifi_cat.parent_path
                    and rec.categ_id.parent_path.startswith(hifi_cat.parent_path)
                )

    def _compute_hifi_unit_count(self):
        for rec in self:
            if rec.is_hifi_device:
                product = rec.product_variant_id
                if product:
                    rec.hifi_unit_count = self.env['stock.lot'].search_count([
                        ('product_id', '=', product.id),
                        ('is_hifi_unit', '=', True),
                    ])
                else:
                    rec.hifi_unit_count = 0
            else:
                rec.hifi_unit_count = 0

    @api.depends("brand_id", "brand_id.name", "name", "is_hifi_device")
    def _compute_display_name(self):
        hifi = self.filtered('is_hifi_device')
        non_hifi = self - hifi
        if non_hifi:
            super(ProductTemplate, non_hifi)._compute_display_name()
        for rec in hifi:
            brand = rec.brand_id.name or ''
            model = rec.name or ''
            rec.display_name = f"{brand} {model}".strip()

    @api.model
    def _name_search(self, name, domain=None, operator='ilike', limit=None, order=None):
        domain = domain or []
        if name:
            search_terms = name.split()
            search_domain = []
            for term in search_terms:
                search_domain += ['|', ('brand_id.name', operator, term), ('name', operator, term)]
            return self._search(search_domain + domain, limit=limit, order=order)
        return super()._name_search(name, domain=domain, operator=operator, limit=limit, order=order)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)

        hifi_cat = self.env.ref('repair_devices.product_category_hifi', raise_if_not_found=False)
        if not hifi_cat:
            return defaults
        default_categ = self.env.context.get('default_categ_id')
        if not default_categ:
            return defaults
        cat = self.env['product.category'].browse(default_categ)
        if not (cat.parent_path and hifi_cat.parent_path
                and cat.parent_path.startswith(hifi_cat.parent_path)):
            return defaults

        # Force HiFi mandatory config — override model defaults (which set 'consu')
        defaults['detailed_type'] = 'product'
        defaults['tracking'] = 'serial'
        defaults['sale_ok'] = True
        if 'rent_ok' in self._fields:
            defaults['rent_ok'] = True

        input_name = self.env.context.get('default_name') or self.env.context.get('default_display_name')

        if input_name and not defaults.get('brand_id'):
            def clean_str(s):
                return re.sub(r'[^a-z0-9]', '', s.lower()) if s else ''

            input_clean = clean_str(input_name)
            brands = self.env['repair.device.brand'].search([])
            sorted_brands = sorted(brands, key=lambda b: len(clean_str(b.name)), reverse=True)

            for brand in sorted_brands:
                brand_clean = clean_str(brand.name)
                if brand_clean and input_clean.startswith(brand_clean):
                    defaults['brand_id'] = brand.id
                    target_length = len(brand_clean)
                    current_count = 0
                    cut_index = 0
                    for i, char in enumerate(input_name):
                        if char.isalnum():
                            current_count += 1
                        if current_count == target_length:
                            cut_index = i + 1
                            break
                    remainder = input_name[cut_index:].strip()
                    remainder = re.sub(r'^[^a-zA-Z0-9]+', '', remainder)
                    defaults['name'] = remainder.upper()
                    break

        return defaults

    def action_view_lots(self):
        """Open stock.lot records for this HiFi device."""
        self.ensure_one()
        product = self.product_variant_id
        return {
            'type': 'ir.actions.act_window',
            'name': 'Appareils physiques',
            'res_model': 'stock.lot',
            'view_mode': 'tree,form',
            'domain': [('product_id', '=', product.id), ('is_hifi_unit', '=', True)],
            'context': {
                'default_product_id': product.id,
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        hifi_cat = self.env.ref('repair_devices.product_category_hifi', raise_if_not_found=False)
        if hifi_cat:
            for vals in vals_list:
                categ_id = vals.get('categ_id')
                if categ_id:
                    cat = self.env['product.category'].browse(categ_id)
                    if (cat.parent_path and hifi_cat.parent_path
                            and cat.parent_path.startswith(hifi_cat.parent_path)):
                        vals['detailed_type'] = 'product'
                        vals['tracking'] = 'serial'
                        vals.setdefault('sale_ok', True)
                        if 'rent_ok' in self._fields:
                            vals.setdefault('rent_ok', True)
        return super().create(vals_list)

    def write(self, vals):
        hifi_cat = self.env.ref('repair_devices.product_category_hifi', raise_if_not_found=False)
        if hifi_cat and 'categ_id' in vals:
            cat = self.env['product.category'].browse(vals['categ_id'])
            if (cat.parent_path and hifi_cat.parent_path
                    and cat.parent_path.startswith(hifi_cat.parent_path)):
                vals.setdefault('detailed_type', 'product')
                vals.setdefault('tracking', 'serial')
                vals.setdefault('sale_ok', True)
                if 'rent_ok' in self._fields:
                    vals.setdefault('rent_ok', True)
        return super().write(vals)

    @api.constrains('is_hifi_device', 'tracking', 'detailed_type')
    def _check_hifi_device_config(self):
        for rec in self.filtered('is_hifi_device'):
            if rec.tracking != 'serial':
                raise ValidationError(_(
                    "L'appareil « %s » doit être suivi par numéro de série."
                ) % rec.display_name)
            if rec.detailed_type != 'product':
                raise ValidationError(_(
                    "L'appareil « %s » doit être un article stockable."
                ) % rec.display_name)

    _sql_constraints = [
        ("unique_hifi_brand_model",
         "unique(brand_id, name)",
         "Ce modèle existe déjà pour cette marque."),
    ]
