# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from . import models
from . import controllers
from . import report
from . import wizard

from odoo import api, SUPERUSER_ID


def _create_warehouse_data(env):
    """ This hook is used to add default repair picking types on every warehouse.
    It is necessary if the repair module is installed after some warehouses were already created.
    """
    warehouses = env['stock.warehouse'].search([('repair_type_id', '=', False)])
    for warehouse in warehouses:
        picking_type_vals = warehouse._create_or_update_sequences_and_picking_types()
        if picking_type_vals:
            warehouse.write(picking_type_vals)


def _post_init_tag_repair_orders(env):
    """Tag existing sale.order records linked to repairs as 'repair_quote'."""
    repair_so_ids = env['repair.order'].search([
        ('sale_order_id', '!=', False)
    ]).mapped('sale_order_id')
    if repair_so_ids:
        repair_so_ids.filtered(
            lambda so: so.order_type in (False, 'standard')
        ).write({'order_type': 'repair_quote'})
