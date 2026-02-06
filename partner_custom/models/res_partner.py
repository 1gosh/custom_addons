# -*- coding: utf-8 -*-

from odoo import api, models
from odoo.addons.phone_validation.tools import phone_validation


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.onchange('phone', 'country_id', 'company_id')
    def _onchange_phone_validation(self):
        """Override to use NATIONAL format instead of INTERNATIONAL."""
        if self.phone:
            self.phone = self._phone_format(fname='phone', force_format='NATIONAL') or self.phone

    @api.onchange('mobile', 'country_id', 'company_id')
    def _onchange_mobile_validation(self):
        """Override to use NATIONAL format instead of INTERNATIONAL."""
        if self.mobile:
            self.mobile = self._phone_format(fname='mobile', force_format='NATIONAL') or self.mobile

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-format phone/mobile on record creation."""
        vals_list = [self._format_phone_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        """Auto-format phone/mobile on record update."""
        # Prevent recursion with context flag
        if self.env.context.get('skip_phone_format'):
            return super().write(vals)

        vals = self._format_phone_vals(vals)
        return super().write(vals)

    def _format_phone_vals(self, vals):
        """
        Format phone and mobile fields in vals dict to NATIONAL format.

        Args:
            vals: Dictionary of field values

        Returns:
            Dictionary with formatted phone numbers
        """
        if not vals:
            return vals

        vals = vals.copy()
        country_code = self._get_country_code_for_vals(vals)

        for field in ('phone', 'mobile'):
            if field in vals and vals[field]:
                formatted = self._phone_format_field(vals[field], country_code)
                if formatted:
                    vals[field] = formatted

        return vals

    def _get_country_code_for_vals(self, vals):
        """
        Get country code for formatting from vals or record.

        Priority: vals['country_id'] > self.country_id > company country > 'FR'

        Args:
            vals: Dictionary of field values

        Returns:
            Country code (e.g., 'FR', 'DE')
        """
        if 'country_id' in vals and vals['country_id']:
            country = self.env['res.country'].browse(vals['country_id'])
            return country.code or 'FR'

        if self and self.country_id:
            return self.country_id.code

        return self.env.company.country_id.code or 'FR'

    def _phone_format_field(self, number, country_code):
        """
        Format a phone number to NATIONAL format.

        Args:
            number: Phone number string
            country_code: Country code (e.g., 'FR')

        Returns:
            Formatted phone number or None if formatting fails
        """
        if not number:
            return None

        formatted = phone_validation.phone_format(
            number,
            country_code,
            None,
            force_format='NATIONAL',
            raise_exception=False,
        )

        return formatted

    def action_format_phone_numbers(self):
        """
        Bulk action to format all phone/mobile numbers to NATIONAL format.

        Can be called from server action on selected partners.

        Returns:
            Client action to show notification with count of updated records
        """
        count = 0

        for partner in self:
            vals = {}
            country = partner.country_id or self.env.company.country_id
            country_code = country.code or 'FR'
            country_phone_code = country.phone_code if country else None

            # Format phone field
            if partner.phone:
                # First sanitize to E164 to ensure we have a clean number
                e164 = phone_validation.phone_format(
                    partner.phone,
                    country_code,
                    country_phone_code,
                    force_format='E164',
                    raise_exception=False,
                )
                # Then format to NATIONAL
                if e164:
                    formatted = phone_validation.phone_format(
                        e164,
                        country_code,
                        country_phone_code,
                        force_format='NATIONAL',
                        raise_exception=False,
                    )
                    if formatted and formatted != partner.phone:
                        vals['phone'] = formatted

            # Format mobile field
            if partner.mobile:
                # First sanitize to E164 to ensure we have a clean number
                e164 = phone_validation.phone_format(
                    partner.mobile,
                    country_code,
                    country_phone_code,
                    force_format='E164',
                    raise_exception=False,
                )
                # Then format to NATIONAL
                if e164:
                    formatted = phone_validation.phone_format(
                        e164,
                        country_code,
                        country_phone_code,
                        force_format='NATIONAL',
                        raise_exception=False,
                    )
                    if formatted and formatted != partner.mobile:
                        vals['mobile'] = formatted

            # Update partner if any field was formatted
            if vals:
                partner.with_context(skip_phone_format=True).write(vals)
                count += 1

        # Return notification
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Formatage terminé',
                'message': f'{count} contact(s) formaté(s) en NATIONAL.',
                'type': 'success',
            }
        }
