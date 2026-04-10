# -*- coding: utf-8 -*-
"""Post-migration for repair_custom 17.0.1.2.5.

Completes the cross-module data migration that repair_devices 2.4 post-migrate
could not finish: at that point, repair_custom columns (stock_state, sale_date,
warranty fields on stock.lot; lot_id on repair.order) did not yet exist.

This script runs after repair_custom's schema update has created those columns,
so all target columns are now available.

Steps:
  A. Copy warranty/stock fields from repair_device_unit → stock.lot
  B. Populate repair_order.lot_id + product_tmpl_id via serial_number matching
  C. Populate sale_order_line.lot_id via sale_order_id matching
  D. Seed quants at correct stock locations based on migrated stock_state
  E. Force recompute stock_state on all HiFi lots
"""
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# Mapping: old stock_state → xml_id of target location (for quant seeding)
STATE_LOCATION_MAP = {
    'client': 'stock.stock_location_customers',
    'sold': 'stock.stock_location_customers',
    'stock': 'repair_custom.stock_location_ateliers',
    'in_repair': 'repair_custom.stock_location_ateliers',
    'rented': 'repair_custom.stock_location_rented',
}


def _table_exists(cr, table):
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables WHERE table_name = %s
        )
    """, [table])
    return cr.fetchone()[0]


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        )
    """, [table, column])
    return cr.fetchone()[0]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Guard: nothing to do on fresh install (old tables won't exist)
    if not _table_exists(cr, 'repair_device_unit'):
        _logger.info("17.0.1.2.5: repair_device_unit table absent — skipping (fresh install).")
        return

    _logger.info("17.0.1.2.5: starting cross-module data migration...")

    # --- Step A: Copy warranty/stock fields from repair_device_unit → stock.lot ---
    if _column_exists(cr, 'stock_lot', 'stock_state'):
        cr.execute("""
            UPDATE stock_lot sl
            SET stock_state = rdu.stock_state,
                sale_date = rdu.sale_date,
                sav_expiry = rdu.sav_expiry,
                sale_order_id = rdu.sale_order_id,
                last_delivered_repair_id = rdu.last_delivered_repair_id,
                sar_expiry = rdu.sar_expiry
            FROM repair_device_unit rdu
            WHERE rdu.lot_id = sl.id
              AND sl.is_hifi_unit = TRUE
        """)
        _logger.info("Step A: copied warranty/stock_state fields to stock.lot: %d rows", cr.rowcount)
    else:
        _logger.warning("Step A: stock_state column missing on stock_lot — skipping")

    # --- Step B: Populate repair_order.lot_id via serial_number matching ---
    if _column_exists(cr, 'repair_order', 'lot_id'):
        cr.execute("""
            UPDATE repair_order ro
            SET lot_id = sl.id,
                product_tmpl_id = COALESCE(ro.product_tmpl_id, pp.product_tmpl_id)
            FROM stock_lot sl
            JOIN product_product pp ON pp.id = sl.product_id
            WHERE sl.name = ro.serial_number
              AND sl.is_hifi_unit = TRUE
              AND ro.lot_id IS NULL
              AND ro.serial_number IS NOT NULL
        """)
        _logger.info("Step B: populated repair_order.lot_id via serial_number: %d rows", cr.rowcount)
    else:
        _logger.warning("Step B: lot_id column missing on repair_order — skipping")

    # --- Step C: Populate sale_order_line.lot_id via sale_order_id matching ---
    if _column_exists(cr, 'sale_order_line', 'lot_id'):
        cr.execute("""
            UPDATE sale_order_line sol
            SET lot_id = rdu.lot_id
            FROM repair_device_unit rdu
            WHERE rdu.sale_order_id = sol.order_id
              AND sol.lot_id IS NULL
              AND rdu.lot_id IS NOT NULL
        """)
        _logger.info("Step C: populated sale_order_line.lot_id: %d rows", cr.rowcount)
    else:
        _logger.warning("Step C: lot_id column missing on sale_order_line — skipping")

    # --- Step D: Seed quants at correct stock locations ---
    cr.execute("""
        SELECT id, stock_state
        FROM stock_lot
        WHERE is_hifi_unit = TRUE
          AND stock_state IS NOT NULL
    """)
    rows = cr.fetchall()
    if not rows:
        _logger.info("Step D: no HiFi lots with stock_state found — skipping quant seeding.")
    else:
        _logger.info("Step D: seeding quants for %d lots", len(rows))

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

        _logger.info("Step D: seeded quants for %d lots", seeded)

    # --- Step E: Force recompute stock_state on all HiFi lots ---
    Lot = env['stock.lot']
    all_hifi_lots = Lot.search([('is_hifi_unit', '=', True)])
    if all_hifi_lots:
        all_hifi_lots._compute_stock_state()
        _logger.info("Step E: recomputed stock_state for %d lots", len(all_hifi_lots))

    _logger.info("17.0.1.2.5: cross-module data migration complete.")
