# -*- coding: utf-8 -*-
{
    'name': 'Partner Customizations',
    'version': '17.0.1.0.0',
    'category': 'Contact Management',
    'summary': 'Custom partner display name formatting and form layout',
    'description': """
Partner Customizations
======================

This module customizes the partner (res.partner) model:

* **Display Name Formatting:**
  - Email displayed on new line instead of inline <email> format
  - Phone number added to display name when show_phone context is set

* **Form Layout:**
  - Contact information (phone, email, website) in left column
  - Address and company association in right column
  - Function field only visible when partner is associated with a company
    """,
    'author': 'Custom',
    'depends': ['base', 'account'],
    'data': [
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
