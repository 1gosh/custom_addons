{
    "name": "Repair Devices",
    "version": "1.0",
    "summary": "Catalogue d’appareils Hi-Fi pour les ordres de réparation",
    "author": "martinl",
    "depends": [
        "base",
    ],
    "data": [
        'security/ir.model.access.csv',
        "views/device_views.xml",
        "views/menu.xml",
        "views/repair_device_reclassify_views.xml",
    ],
    'installable': True,
    'application': True,
}