# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'repair_custom',
    'version': '17.0.1.1.0',
    'category': 'Inventory/Inventory',
    'summary': 'Custom repair management for workshop',
    "author": "martinl",
    'description': """
The aim is to have a complete module to manage all products repairs.
====================================================================

The following topics are covered by this module:
------------------------------------------------------
    * Add/remove products in the reparation
    * Impact for stocks
    * Warranty concept
    * Repair quotation report
    * Notes for the technician and for the final customer
""",
    'depends': ['repair_devices', 'web', 'stock', 'sale_management', 'account'],
    'post_init_hook': '_post_init_tag_repair_orders',
    'data': [
        'security/ir.model.access.csv',
        'security/repair_security.xml',
        'views/repair_views.xml',
        'views/sale_order_views.xml',
        'views/sale_unit_wizard_views.xml',
        'views/tracking_views.xml',
        'views/repair_device_views.xml',
        'views/account_move_views.xml',
        'views/repair_invoice_template_views.xml',
        'views/repair_pricing_wizard_views.xml',
        'views/repair_notes_template_views.xml',
        'views/repair_manager_wizard_views.xml',
        'report/repair_reports.xml',
        'report/repairorder_final.xml',
        "report/paper_formats.xml",
        "report/repair_ticket.xml",
        "report/repair_label.xml",
        "report/custom_invoice.xml",
        'data/repair_order_sequence.xml',
        'data/sale_order_template_data.xml',
        'data/repair_data.xml',
        'data/dashboard_data.xml',
        'data/mail_activity_data.xml',
        'data/stock_data.xml',
        'data/cron_data.xml',
        'data/account_tax_data.xml',
        'data/account_fiscal_position_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'repair_custom/static/src/css/views.css',
        ],
    },
    'installable': True,
    'application': True,
}
