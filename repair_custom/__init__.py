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
    """Tag existing sale.order records linked to repairs with repair_quote template."""
    repair_quote_template = env.ref('repair_custom.sale_order_template_repair_quote', raise_if_not_found=False)
    if not repair_quote_template:
        return

    repair_so_ids = env['repair.order'].search([
        ('sale_order_id', '!=', False)
    ]).mapped('sale_order_id')

    if repair_so_ids:
        repair_so_ids.filtered(
            lambda so: not so.sale_order_template_id
        ).write({'sale_order_template_id': repair_quote_template.id})
