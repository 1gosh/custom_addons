{
    "name": "Repair Devices",
    "version": "1.0",
    "summary": "Catalogue d’appareils Hi-Fi pour les ordres de réparation",
    "author": "martinl",
    "depends": [
        "base",
        'stock',       # <--- INDISPENSABLE pour stock.lot
        'product',     # <--- INDISPENSABLE pour product.product
        'sale_management',
    ],
    "data": [
        'security/ir.model.access.csv',
        "views/device_views.xml",
        "views/menu.xml",
        "views/repair_device_reclassify_views.xml",
    ],
    'post_init_hook': '_post_init_sync_products',
    'installable': True,
    'application': True,
}