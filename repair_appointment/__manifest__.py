# -*- coding: utf-8 -*-
{
    'name': 'repair_appointment',
    'version': '17.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Pickup appointment scheduling for repair batches',
    'author': 'martinl',
    'description': """
Repair Pickup Appointment System
================================
Provides a pickup appointment model tied to repair batches, a client
portal with token-magic-link auth, a backend calendar view, and an
automated reminder CRON with manager-group escalation. Integrates with
the upcoming completion/pickup/SAR workflow via a single hook on
repair.batch.
""",
    'depends': ['repair_custom', 'mail', 'portal'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/mail_activity_type_data.xml',
        'data/pickup_schedule_data.xml',
    ],
    'installable': True,
    'application': False,
}
