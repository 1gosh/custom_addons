# -*- coding: utf-8 -*-

import re
from odoo import api, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.depends('complete_name', 'email', 'vat', 'state_id', 'country_id', 'commercial_company_name')
    @api.depends_context('show_address', 'partner_show_db_id', 'address_inline', 'show_email', 'show_vat', 'show_phone', 'lang')
    def _compute_display_name(self):
        """
        Override display name computation to:
        - Display email on new line instead of inline <email> format
        - Add phone number when show_phone context is set
        """
        for partner in self:
            name = partner.with_context(lang=self.env.lang)._get_complete_name()
            if partner._context.get('show_address'):
                name = name + "\n" + partner._display_address(without_company=True)
            name = re.sub(r'\s+\n', '\n', name)
            if partner._context.get('partner_show_db_id'):
                name = f"{name} ({partner.id})"
            if partner._context.get('address_inline'):
                splitted_names = name.split("\n")
                name = ", ".join([n for n in splitted_names if n.strip()])
            if partner._context.get('show_email') and partner.email:
                name = name + "\n" + partner.email
            if partner._context.get('show_phone') and partner.phone:
                name = name + "\n" + partner.phone
            if partner._context.get('show_vat') and partner.vat:
                name = f"{name} ‒ {partner.vat}"

            partner.display_name = name.strip()
