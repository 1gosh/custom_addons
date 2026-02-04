from . import models
from . import wizard

from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)


def _post_init_sync_products(env):
    """Create product.template for existing repair.device records that don't have one."""
    _logger.info("Running post_init_hook: Syncing products for repair.device...")
    devices = env['repair.device'].search([('product_tmpl_id', '=', False)])
    if devices:
        _logger.info(f"Found {len(devices)} devices without linked products. Syncing...")
        try:
            devices._sync_product_template()
            _logger.info(f"Successfully synced {len(devices)} products.")
        except Exception as e:
            _logger.error(f"Error syncing products: {e}", exc_info=True)
    else:
        _logger.info("All devices already have linked products.")
