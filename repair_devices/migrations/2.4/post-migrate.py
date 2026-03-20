"""Post-migration script for repair_devices 2.0.

Migrates data from old custom tables (repair.device, repair.device.unit)
into extended native Odoo tables (product.template, stock.lot).

This runs on module update (-u), unlike post_init_hook which only runs on
first install. The logic is identical to _post_init_migrate_devices in __init__.py.
"""

from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Reuse the same migration function from __init__.py
    from odoo.addons.repair_devices import _post_init_migrate_devices
    _post_init_migrate_devices(env)
