{
    "name": "Repair Devices",
    "version": "1.0",
    "summary": "Catalogue d’appareils Hi-Fi pour les ordres de réparation",
    "depends": [
        "base",
    ],
    "data": [
        'security/ir.model.access.csv',
        "views/device_views.xml",
        "views/menu.xml",
    ],
    'pre_init_hook': 'cleanup_old_records',
    'installable': True,
    'application': True,
}