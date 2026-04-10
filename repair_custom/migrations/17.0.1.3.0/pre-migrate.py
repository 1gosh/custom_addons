"""Pre-migration for repair_custom 17.0.1.3.0.

Runs BEFORE repair_custom schema update. Old columns (unit_id, device_id) still
exist on repair_order with FK constraints → repair_device_unit / repair_device.
The archived tables from repair_devices 2.7 post-migrate may cause FK failures
during schema update, so we must drop constraints and save mappings now.

Steps:
  1. Save repair_order device references to temp table (for post-migrate)
  2. Drop FK constraints on unit_id, device_id, variant_id
  3. Save old M2M category data (tags, notes, invoice templates)
"""
import logging

_logger = logging.getLogger(__name__)


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


def _drop_fk_constraints(cr, table, column):
    """Drop all foreign key constraints on a given column."""
    cr.execute("""
        SELECT tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        WHERE tc.table_name = %s
          AND tc.constraint_type = 'FOREIGN KEY'
          AND kcu.column_name = %s
    """, [table, column])
    for (constraint_name,) in cr.fetchall():
        cr.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{constraint_name}"')
        _logger.info("Dropped FK constraint %s on %s.%s", constraint_name, table, column)


def migrate(cr, version):
    _logger.info("17.0.1.3.0 pre-migrate: starting")

    # ── Step 1: Save repair_order device references ──────────────────────
    # The columns unit_id, device_id will become orphan references after
    # schema update. Save the lot mapping (from repair_devices post-migrate)
    # and product_tmpl_id for post-migrate restoration.

    if _table_exists(cr, 'repair_order') and _column_exists(cr, 'repair_order', 'unit_id'):
        # Build the mapping using the temp table from repair_devices post-migrate
        unit_lot_join = ""
        unit_lot_col = "NULL::integer as lot_id"
        if _table_exists(cr, '_repair_unit_lot_map'):
            unit_lot_join = "LEFT JOIN _repair_unit_lot_map ulm ON ulm.unit_id = ro.unit_id"
            unit_lot_col = "ulm.lot_id"

        # Use archived repair_device table if available
        device_table = '_archived_repair_device'
        if not _table_exists(cr, device_table):
            device_table = 'repair_device'
        if not _table_exists(cr, device_table):
            device_table = None

        if device_table:
            device_join = f"LEFT JOIN {device_table} rd ON rd.id = ro.device_id"
            tmpl_col = "rd.product_tmpl_id"
        else:
            device_join = ""
            tmpl_col = "NULL::integer as product_tmpl_id"

        cr.execute("DROP TABLE IF EXISTS _repair_order_device_map")
        cr.execute(f"""
            CREATE TABLE _repair_order_device_map AS
            SELECT ro.id as repair_id, ro.unit_id, ro.device_id,
                   {unit_lot_col}, {tmpl_col}
            FROM repair_order ro
            {unit_lot_join}
            {device_join}
        """)
        _logger.info("Step 1: saved %d repair_order device mappings", cr.rowcount)

    # ── Step 2: Drop FK constraints ──────────────────────────────────────
    # These point to repair_device_unit / repair_device (now archived).
    # Must be dropped before schema update tries to reconcile them.

    for col in ('unit_id', 'device_id', 'variant_id'):
        if _column_exists(cr, 'repair_order', col):
            _drop_fk_constraints(cr, 'repair_order', col)

    # Also drop FK on sale_order_line.device_unit_id if it exists
    if _column_exists(cr, 'sale_order_line', 'device_unit_id'):
        _drop_fk_constraints(cr, 'sale_order_line', 'device_unit_id')

    # ── Step 3: Save old M2M category data ───────────────────────────────
    # The old M2M tables (repair_device_category relations) will become
    # orphans after schema update creates new product_category relations.
    # Save the data with mapped category IDs for post-migrate restoration.

    m2m_tables = [
        {
            'old_table': 'repair_tags_repair_device_category_rel',
            'save_table': '_repair_m2m_tags_category_save',
            'id_col': 'repair_tags_id',
            'cat_col': 'repair_device_category_id',
        },
        {
            'old_table': 'repair_notes_template_repair_device_category_rel',
            'save_table': '_repair_m2m_notes_category_save',
            'id_col': 'repair_notes_template_id',
            'cat_col': 'repair_device_category_id',
        },
        {
            'old_table': 'repair_invoice_template_repair_device_category_rel',
            'save_table': '_repair_m2m_invoice_category_save',
            'id_col': 'repair_invoice_template_id',
            'cat_col': 'repair_device_category_id',
        },
    ]

    has_mapping = _table_exists(cr, '_repair_category_migration_map')

    for m2m in m2m_tables:
        if not _table_exists(cr, m2m['old_table']):
            _logger.info("Step 3: %s not found, skipping", m2m['old_table'])
            continue

        cr.execute(f"DROP TABLE IF EXISTS {m2m['save_table']}")
        if has_mapping:
            cr.execute(f"""
                CREATE TABLE {m2m['save_table']} AS
                SELECT rel.{m2m['id_col']}, m.new_id as product_category_id
                FROM {m2m['old_table']} rel
                JOIN _repair_category_migration_map m ON m.old_id = rel.{m2m['cat_col']}
            """)
        else:
            cr.execute(f"""
                CREATE TABLE {m2m['save_table']} AS
                SELECT rel.{m2m['id_col']}, rel.{m2m['cat_col']} as product_category_id
                FROM {m2m['old_table']} rel
                WHERE FALSE
            """)
        _logger.info("Step 3: saved %d rows from %s", cr.rowcount, m2m['old_table'])

    _logger.info("17.0.1.3.0 pre-migrate: complete.")
