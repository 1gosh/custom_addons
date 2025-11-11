from . import models

from odoo import api, SUPERUSER_ID

def cleanup_old_records(cr):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Exemple : supprimer anciens menus ou vues orphelines
    obsolete_menu_xml_ids = [
        'repair_devices.menu_old_device',
    ]
    for xmlid in obsolete_menu_xml_ids:
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.unlink()