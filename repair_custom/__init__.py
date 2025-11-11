# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from . import models
from . import controllers
from . import report

from odoo import api, SUPERUSER_ID

from odoo import api, SUPERUSER_ID

def cleanup_old_records(cr):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Exemple : supprimer anciens menus ou vues orphelines
    obsolete_menu_xml_ids = [
        'repair_custom.menu_old_dashboard',
    ]
    for xmlid in obsolete_menu_xml_ids:
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.unlink()

def _create_warehouse_data(env):
    """ This hook is used to add default repair picking types on every warehouse.
    It is necessary if the repair module is installed after some warehouses were already created.
    """
    warehouses = env['stock.warehouse'].search([('repair_type_id', '=', False)])
    for warehouse in warehouses:
        picking_type_vals = warehouse._create_or_update_sequences_and_picking_types()
        if picking_type_vals:
            warehouse.write(picking_type_vals)
