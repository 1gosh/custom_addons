# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


{
    'name': 'repair_custom',
    'version': '1.0',
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
    'data': [
        'security/ir.model.access.csv',
        'security/repair_security.xml',
        'views/repair_views.xml',
        'views/sale_order_views.xml',
        'views/tracking_views.xml',
        'views/repair_device_views.xml',
        'views/repair_order_sequence.xml',
        'views/account_move_views.xml',
        'report/repair_reports.xml',
        'report/repairorder_final.xml',
        'data/repair_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'repair_custom/static/src/css/views.css',
        ],
    },
    'installable': True,
    'application': True,
}
