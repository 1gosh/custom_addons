# -*- coding: utf-8 -*-
"""Phase 2 migration: seed quants for existing HiFi lots.

Reads the old stock_state column (still in PostgreSQL after field becomes
computed) and creates quants at the appropriate stock locations so the
new computed stock_state can derive the correct value.
"""
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# Mapping: old stock_state → xml_id of target location
STATE_LOCATION_MAP = {
    'client': 'stock.stock_location_customers',
    'sold': 'stock.stock_location_customers',
    'stock': 'repair_custom.stock_location_ateliers',
    'in_repair': 'repair_custom.stock_location_ateliers',
    'rented': 'repair_custom.stock_location_rented',
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Read old stock_state values directly from PostgreSQL
    cr.execute("""
        SELECT id, stock_state
        FROM stock_lot
        WHERE is_hifi_unit = TRUE
          AND stock_state IS NOT NULL
    """)
    rows = cr.fetchall()
    if not rows:
        _logger.info("Phase 2 migration: no HiFi lots found, skipping.")
        return

    _logger.info("Phase 2 migration: seeding quants for %d lots", len(rows))

    Quant = env['stock.quant']
    Lot = env['stock.lot']

    # Resolve locations once
    locations = {}
    for state, xmlid in STATE_LOCATION_MAP.items():
        loc = env.ref(xmlid, raise_if_not_found=False)
        if loc:
            locations[state] = loc

    seeded = 0
    for lot_id, old_state in rows:
        lot = Lot.browse(lot_id)
        if not lot.exists() or not lot.product_id:
            continue

        # Skip if lot already has a positive quant
        existing = Quant.search([
            ('lot_id', '=', lot.id),
            ('quantity', '>', 0),
        ], limit=1)
        if existing:
            continue

        target_loc = locations.get(old_state)
        if not target_loc:
            target_loc = locations.get('client')
        if not target_loc:
            continue

        Quant._update_available_quantity(
            lot.product_id, target_loc, 1.0, lot_id=lot
        )
        seeded += 1

    _logger.info("Phase 2 migration: seeded quants for %d lots", seeded)

    # Force recompute stock_state on all HiFi lots
    all_hifi_lots = Lot.search([('is_hifi_unit', '=', True)])
    if all_hifi_lots:
        all_hifi_lots._compute_stock_state()
        _logger.info("Phase 2 migration: recomputed stock_state for %d lots", len(all_hifi_lots))
