"""Post-migration for repair_custom 17.0.1.3.0.

Runs AFTER repair_custom schema update. New columns now exist:
  - repair_order: lot_id, product_tmpl_id (→ product.template)
  - stock.lot: stock_state, sale_date, sav_expiry, sale_order_id,
               last_delivered_repair_id, sar_expiry, functional_state

Steps:
  1. Populate repair_order.lot_id + product_tmpl_id from saved mapping
  2. Copy warranty/stock fields from repair_device_unit → stock.lot
  3. Populate sale_order_line.lot_id
  4. Restore M2M category data (tags, notes, invoice templates)
  5. Seed quants from stock_state
  6. Recompute stock_state + cleanup
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

    _logger.info("17.0.1.3.0 post-migrate: starting")

    # ── Step 1: Populate repair_order.lot_id + product_tmpl_id ───────────

    if _table_exists(cr, '_repair_order_device_map'):
        cr.execute("""
            UPDATE repair_order ro
            SET lot_id = dm.lot_id,
                product_tmpl_id = dm.product_tmpl_id
            FROM _repair_order_device_map dm
            WHERE dm.repair_id = ro.id
              AND dm.lot_id IS NOT NULL
        """)
        _logger.info("Step 1: populated lot_id/product_tmpl_id: %d rows", cr.rowcount)

        # For orders without a unit mapping, try product_tmpl_id alone
        cr.execute("""
            UPDATE repair_order ro
            SET product_tmpl_id = dm.product_tmpl_id
            FROM _repair_order_device_map dm
            WHERE dm.repair_id = ro.id
              AND ro.product_tmpl_id IS NULL
              AND dm.product_tmpl_id IS NOT NULL
        """)
        if cr.rowcount:
            _logger.info("Step 1: set product_tmpl_id (no lot): %d rows", cr.rowcount)
    else:
        _logger.warning("Step 1: _repair_order_device_map not found — skipping")

    # ── Step 1b: Fallback — match via serial_number → stock_lot ─────────
    # Catches repair orders missed by the mapping (e.g., devices that had
    # NULL product_tmpl_id before pre-migrate fixed them).

    cr.execute("""
        UPDATE repair_order ro
        SET lot_id = sl.id
        FROM stock_lot sl
        WHERE sl.name = ro.serial_number
          AND sl.is_hifi_unit = TRUE
          AND ro.lot_id IS NULL
          AND ro.serial_number IS NOT NULL
          AND ro.serial_number != ''
    """)
    if cr.rowcount:
        _logger.info("Step 1b: fallback lot_id via serial_number match: %d rows", cr.rowcount)

    # ── Step 1c: Derive product_tmpl_id from lot_id ─────────────────────

    cr.execute("""
        UPDATE repair_order ro
        SET product_tmpl_id = pp.product_tmpl_id
        FROM stock_lot sl
        JOIN product_product pp ON pp.id = sl.product_id
        WHERE sl.id = ro.lot_id
          AND ro.product_tmpl_id IS NULL
          AND ro.lot_id IS NOT NULL
    """)
    if cr.rowcount:
        _logger.info("Step 1c: derived product_tmpl_id from lot_id: %d rows", cr.rowcount)

    # ── Step 2: Copy warranty/stock fields → stock.lot ───────────────────
    # repair_device_unit had stock_state, sale_date, sav_expiry, etc.
    # These fields are now on stock.lot (added by repair_custom schema update).

    unit_table = '_archived_repair_device_unit'
    if not _table_exists(cr, unit_table):
        unit_table = 'repair_device_unit'

    if _table_exists(cr, unit_table) and _table_exists(cr, '_repair_unit_lot_map'):
        # Check which columns exist on the old unit table
        warranty_cols = []
        col_mapping = {
            'stock_state': 'stock_state',
            'sale_date': 'sale_date',
            'sav_expiry': 'sav_expiry',
            'sale_order_id': 'sale_order_id',
            'last_delivered_repair_id': 'last_delivered_repair_id',
            'sar_expiry': 'sar_expiry',
            'functional_state': 'functional_state',
        }
        for col in col_mapping:
            if _column_exists(cr, unit_table.replace('_archived_', ''), col) or \
               _column_exists(cr, unit_table, col):
                warranty_cols.append(col)

        if warranty_cols:
            set_clause = ', '.join(f'{col} = rdu.{col}' for col in warranty_cols)
            cr.execute(f"""
                UPDATE stock_lot sl
                SET {set_clause}
                FROM {unit_table} rdu
                JOIN _repair_unit_lot_map ulm ON ulm.unit_id = rdu.id
                WHERE sl.id = ulm.lot_id
            """)
            _logger.info("Step 2: copied warranty/stock fields (%s): %d rows",
                         ', '.join(warranty_cols), cr.rowcount)
        else:
            _logger.info("Step 2: no warranty columns found on %s", unit_table)
    else:
        _logger.info("Step 2: unit table or mapping not found — skipping")

    # ── Step 3: Populate sale_order_line.lot_id ──────────────────────────

    if _table_exists(cr, '_repair_unit_lot_map') and \
       _column_exists(cr, 'sale_order_line', 'lot_id'):

        # Via device_unit_id if the column exists
        if _column_exists(cr, 'sale_order_line', 'device_unit_id'):
            cr.execute("""
                UPDATE sale_order_line sol
                SET lot_id = ulm.lot_id
                FROM _repair_unit_lot_map ulm
                WHERE ulm.unit_id = sol.device_unit_id
                  AND sol.lot_id IS NULL
                  AND ulm.lot_id IS NOT NULL
            """)
            _logger.info("Step 3a: populated SOL lot_id via device_unit_id: %d rows", cr.rowcount)

        # Via sale_order_id matching
        if _table_exists(cr, unit_table):
            cr.execute(f"""
                UPDATE sale_order_line sol
                SET lot_id = ulm.lot_id
                FROM {unit_table} rdu
                JOIN _repair_unit_lot_map ulm ON ulm.unit_id = rdu.id
                WHERE rdu.sale_order_id = sol.order_id
                  AND sol.lot_id IS NULL
                  AND ulm.lot_id IS NOT NULL
            """)
            _logger.info("Step 3b: populated SOL lot_id via sale_order_id: %d rows", cr.rowcount)

    # ── Step 4: Restore M2M category data ────────────────────────────────

    m2m_restore = [
        {
            'save_table': '_repair_m2m_tags_category_save',
            'new_table': 'repair_tags_product_category_rel',
            'id_col': 'repair_tags_id',
            'cat_col': 'product_category_id',
        },
        {
            'save_table': '_repair_m2m_notes_category_save',
            'new_table': 'repair_notes_template_product_category_rel',
            'id_col': 'repair_notes_template_id',
            'cat_col': 'product_category_id',
        },
        {
            'save_table': '_repair_m2m_invoice_category_save',
            'new_table': 'repair_invoice_template_product_category_rel',
            'id_col': 'repair_invoice_template_id',
            'cat_col': 'product_category_id',
        },
    ]

    for m2m in m2m_restore:
        if not _table_exists(cr, m2m['save_table']):
            continue
        cr.execute(f"""
            INSERT INTO {m2m['new_table']} ({m2m['id_col']}, {m2m['cat_col']})
            SELECT {m2m['id_col']}, product_category_id
            FROM {m2m['save_table']}
            ON CONFLICT DO NOTHING
        """)
        _logger.info("Step 4: restored %s: %d rows", m2m['new_table'], cr.rowcount)

    # ── Step 5: Seed quants from stock_state ─────────────────────────────

    cr.execute("""
        SELECT id, stock_state
        FROM stock_lot
        WHERE is_hifi_unit = TRUE
          AND stock_state IS NOT NULL
    """)
    rows = cr.fetchall()

    if rows:
        _logger.info("Step 5: seeding quants for %d lots", len(rows))

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

            target_loc = locations.get(old_state) or locations.get('client')
            if not target_loc:
                continue

            Quant._update_available_quantity(
                lot.product_id, target_loc, 1.0, lot_id=lot
            )
            seeded += 1

        _logger.info("Step 5: seeded quants for %d lots", seeded)
    else:
        _logger.info("Step 5: no HiFi lots with stock_state — skipping")

    # ── Step 6: Recompute stock_state + cleanup ──────────────────────────

    all_hifi_lots = env['stock.lot'].search([('is_hifi_unit', '=', True)])
    if all_hifi_lots:
        all_hifi_lots._compute_stock_state()
        _logger.info("Step 6: recomputed stock_state for %d lots", len(all_hifi_lots))

    # Drop temp tables
    for temp_table in [
        '_repair_category_migration_map',
        '_repair_unit_lot_map',
        '_repair_order_device_map',
        '_repair_m2m_tags_category_save',
        '_repair_m2m_notes_category_save',
        '_repair_m2m_invoice_category_save',
    ]:
        if _table_exists(cr, temp_table):
            cr.execute(f'DROP TABLE {temp_table}')
            _logger.info("Step 6: dropped temp table %s", temp_table)

    _logger.info("17.0.1.3.0 post-migrate: complete.")
