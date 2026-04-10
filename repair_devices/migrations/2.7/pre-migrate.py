"""Pre-migration for repair_devices 2.7.

Converts repair.device.category records to product.category records,
then remaps every FK/M2M that pointed at the old model so the data
survives the model deletion.

Note: In Odoo 17, translatable fields (translate=True) are stored as JSONB
in PostgreSQL. We use the ORM for product.category creation so that Odoo
handles serialization automatically, and extract plain strings from the
JSONB name dict when needed for raw SQL operations.
"""
import logging

_logger = logging.getLogger(__name__)


def _name_from_raw(name_raw):
    """Extract a plain string from a JSONB name value (dict) or plain string."""
    if isinstance(name_raw, dict):
        return (name_raw.get('fr_FR') or name_raw.get('en_US')
                or next(iter(name_raw.values()), '') or '')
    return name_raw or ''


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    _logger.info("2.7 pre-migrate: starting repair.device.category → product.category consolidation")

    # Guard: nothing to do on a fresh install
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'repair_device_category'
        )
    """)
    if not cr.fetchone()[0]:
        _logger.info("repair_device_category table absent — fresh install, skipping.")
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # 1. Resolve the HiFi root product.category
    #    env.ref() won't work here because the xmlid is registered by
    #    product_category_data.xml which loads AFTER pre-migrate scripts.
    #    Use raw SQL lookup: first try ir_model_data, then fallback to name.
    cr.execute("""
        SELECT res_id FROM ir_model_data
        WHERE module = 'repair_devices' AND name = 'product_category_hifi'
        LIMIT 1
    """)
    row = cr.fetchone()
    if row:
        hifi_root_id = row[0]
    else:
        cr.execute("SELECT id FROM product_category WHERE name = 'Appareils Hi-Fi' LIMIT 1")
        row = cr.fetchone()
        if row:
            hifi_root_id = row[0]
        else:
            _logger.error("HiFi root product.category not found — aborting pre-migrate.")
            return
    _logger.info("Resolved HiFi root category id=%d", hifi_root_id)

    # 2. Load all repair.device.category rows ordered by parent_path
    #    so parents are always processed before children
    cr.execute("""
        SELECT id, name, parent_id, parent_path
        FROM repair_device_category
        ORDER BY parent_path
    """)
    old_cats = cr.fetchall()
    _logger.info("Found %d repair.device.category records to migrate", len(old_cats))

    if not old_cats:
        return

    # 3. Build old→new mapping by creating product.category records via ORM
    #    (ORM handles JSONB serialization and _parent_store parent_path recompute)
    old_to_new = {}  # old repair.device.category.id → new product.category.id

    for (old_id, name_raw, old_parent_id, parent_path) in old_cats:
        name_str = _name_from_raw(name_raw)
        new_parent_id = old_to_new.get(old_parent_id, hifi_root_id)

        # Idempotency: check if a category with this name already exists under new_parent_id
        existing = env['product.category'].search([
            ('name', '=', name_str),
            ('parent_id', '=', new_parent_id),
        ], limit=1)

        if existing:
            new_id = existing.id
            _logger.info("  Reusing existing product.category id=%d for '%s'", new_id, name_str)
        else:
            new_cat = env['product.category'].create({
                'name': name_str,
                'parent_id': new_parent_id,
            })
            new_id = new_cat.id
            _logger.info("  Created product.category id=%d for old id=%d '%s'", new_id, old_id, name_str)

        old_to_new[old_id] = new_id

    # 4. Update product_template.categ_id based on hifi_category_id mapping
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = 'product_template' AND column_name = 'hifi_category_id'
        )
    """)
    has_hifi_cat_col = cr.fetchone()[0]

    if has_hifi_cat_col:
        for old_id, new_id in old_to_new.items():
            cr.execute("""
                UPDATE product_template
                SET categ_id = %s
                WHERE hifi_category_id = %s
            """, [new_id, old_id])
            if cr.rowcount:
                _logger.info(
                    "  Updated %d product_template rows: hifi_category_id=%d → categ_id=%d",
                    cr.rowcount, old_id, new_id,
                )

    # 5. Update repair_order.category_id — drop FK constraint FIRST, then update values
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns
            WHERE table_name = 'repair_order' AND column_name = 'category_id'
        )
    """)
    if cr.fetchone()[0]:
        # Drop FK constraint before updating so we can write product.category IDs
        cr.execute("""
            SELECT tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_name = kcu.table_name
            WHERE tc.table_name = 'repair_order'
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name = 'category_id'
        """)
        for (cname,) in cr.fetchall():
            cr.execute(f'ALTER TABLE repair_order DROP CONSTRAINT IF EXISTS "{cname}"')
            _logger.info("  Dropped FK constraint: %s", cname)

        for old_id, new_id in old_to_new.items():
            cr.execute("""
                UPDATE repair_order SET category_id = %s WHERE category_id = %s
            """, [new_id, old_id])

    # 6. Migrate M2M tables
    m2m_migrations = [
        (
            'repair_tags_repair_device_category_rel',
            'repair_tags_id', 'repair_device_category_id',
            'repair_tags_product_category_rel',
            'repair_tags_id', 'product_category_id',
        ),
        (
            'repair_notes_template_repair_device_category_rel',
            'repair_notes_template_id', 'repair_device_category_id',
            'repair_notes_template_product_category_rel',
            'repair_notes_template_id', 'product_category_id',
        ),
        (
            'repair_invoice_template_repair_device_category_rel',
            'repair_invoice_template_id', 'repair_device_category_id',
            'repair_invoice_template_product_category_rel',
            'repair_invoice_template_id', 'product_category_id',
        ),
    ]

    for (old_tbl, old_left, old_right, new_tbl, new_left, new_right) in m2m_migrations:
        cr.execute("""
            SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)
        """, [old_tbl])
        if not cr.fetchone()[0]:
            _logger.info("  M2M table %s not found — skipping", old_tbl)
            continue

        cr.execute(f"SELECT {old_left}, {old_right} FROM {old_tbl}")
        old_rows = cr.fetchall()
        if not old_rows:
            continue

        cr.execute(f"""
            CREATE TABLE IF NOT EXISTS {new_tbl} (
                {new_left} integer NOT NULL,
                {new_right} integer NOT NULL,
                PRIMARY KEY ({new_left}, {new_right})
            )
        """)

        inserted = 0
        for left_id, old_right_id in old_rows:
            new_right_id = old_to_new.get(old_right_id)
            if not new_right_id:
                continue
            cr.execute(f"""
                INSERT INTO {new_tbl} ({new_left}, {new_right})
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, [left_id, new_right_id])
            inserted += cr.rowcount

        _logger.info("  Migrated %d rows: %s → %s", inserted, old_tbl, new_tbl)

    _logger.info("2.7 pre-migrate: complete.")
