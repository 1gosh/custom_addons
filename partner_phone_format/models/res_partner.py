from odoo import models, api, fields
from odoo.addons.phone_validation.tools import phone_validation


class ResPartner(models.Model):
    _inherit = "res.partner"

    country_id = fields.Many2one(
        'res.country',
        default=lambda self: self.env.ref('base.fr', raise_if_not_found=False)
    )

    @api.onchange('phone', 'country_id', 'company_id')
    def _onchange_phone_auto_format(self):
        """Format phone number on change."""
        for rec in self:
            if rec.phone:
                formatted = phone_validation.phone_format(
                    rec.phone,
                    rec.country_id.code or rec.env.company.country_id.code or 'FR',
                    None,
                    force_format='NATIONAL',
                    raise_exception=False,
                )
                if formatted:
                    rec.phone = formatted

    @api.onchange('mobile', 'country_id', 'company_id')
    def _onchange_mobile_auto_format(self):
        """Format mobile number on change."""
        for rec in self:
            if rec.mobile:
                formatted = phone_validation.phone_format(
                    rec.mobile,
                    rec.country_id.code or rec.env.company.country_id.code or 'FR',
                    None,
                    force_format='NATIONAL',
                    raise_exception=False,
                )
                if formatted:
                    rec.mobile = formatted

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-format phone numbers on create."""
        for vals in vals_list:
            country_code = self._get_country_code_for_vals(vals)
            self._format_phone_vals(vals, country_code)
        return super().create(vals_list)

    def write(self, vals):
        """Auto-format phone numbers on write."""
        if not vals.get('phone') and not vals.get('mobile'):
            return super().write(vals)

        new_country_id = vals.get('country_id')
        countries = self.mapped('country_id')

        # Batch write if all records share same country
        if new_country_id or len(countries) <= 1:
            country_code = self._get_batch_country_code(new_country_id, countries)
            self._format_phone_vals(vals, country_code)
            return super().write(vals)

        # Multiple countries: format per record
        for record in self:
            record_vals = vals.copy()
            country_code = record.country_id.code or self.env.company.country_id.code or 'FR'
            self._format_phone_vals(record_vals, country_code)
            super(ResPartner, record).write(record_vals)
        return True

    def _get_country_code_for_vals(self, vals):
        """Get country code from vals or default."""
        if vals.get('country_id'):
            return self.env['res.country'].browse(vals['country_id']).code
        return self.env.company.country_id.code or 'FR'

    def _get_batch_country_code(self, new_country_id, countries):
        """Get country code for batch operations."""
        if new_country_id:
            return self.env['res.country'].browse(new_country_id).code
        if countries:
            return countries[0].code
        return self.env.company.country_id.code or 'FR'

    def _format_phone_vals(self, vals, country_code):
        """Format phone/mobile in vals dict in-place."""
        for field in ('phone', 'mobile'):
            if vals.get(field):
                formatted = phone_validation.phone_format(
                    vals[field],
                    country_code,
                    None,
                    force_format='NATIONAL',
                    raise_exception=False,
                )
                if formatted:
                    vals[field] = formatted
