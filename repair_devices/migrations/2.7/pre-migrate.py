"""Pre-migration for repair_devices 2.7.

Runs BEFORE schema update. Prepares data that must be in place before Odoo
adds new columns and recomputes stored fields.

Steps:
  1. Create HIFI root product.category + register xmlid
  2. Migrate repair.device.category → product.category (under HIFI root)
  3. Set product_template.categ_id from category mapping
  4. Set product_template.name from repair_device.name (model name only)
"""
import logging

_logger = logging.getLogger(__name__)


def _name_from_raw(name_raw):
    """Extract a plain string from a JSONB name value (dict) or plain string.

    In Odoo 17, translatable fields (translate=True) are stored as JSONB.
    """
    if isinstance(name_raw, dict):
        return (name_raw.get('fr_FR') or name_raw.get('en_US')
                or next(iter(name_raw.values()), '') or '')
    return name_raw or ''


def _table_exists(cr, table):
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables WHERE table_name = %s
        )
    """, [table])
    return cr.fetchone()[0]


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    _logger.info("2.7 pre-migrate: starting")

    # ── Step 1: Create HIFI root category + register xmlid ──────────────

    hifi_cat_id = None

    cr.execute("""
        SELECT res_id FROM ir_model_data
        WHERE module = 'repair_devices' AND name = 'product_category_hifi'
        LIMIT 1
    """)
    row = cr.fetchone()
    if row:
        # Verify record actually exists
        cr.execute("SELECT id FROM product_category WHERE id = %s", [row[0]])
        if cr.fetchone():
            hifi_cat_id = row[0]
            _logger.info("Found HIFI category id=%d via xmlid", hifi_cat_id)
        else:
            cr.execute("""
                DELETE FROM ir_model_data
                WHERE module = 'repair_devices' AND name = 'product_category_hifi'
            """)
            _logger.warning("Stale xmlid removed (pointed to deleted record)")

    if not hifi_cat_id:
        cr.execute("SELECT id FROM product_category WHERE name = 'HIFI' LIMIT 1")
        row = cr.fetchone()
        if row:
            hifi_cat_id = row[0]
            _logger.info("Found HIFI category id=%d by name", hifi_cat_id)
        else:
            cr.execute("""
                INSERT INTO product_category
                    (name, parent_path, create_uid, write_uid, create_date, write_date)
                VALUES ('HIFI', '', 1, 1, NOW(), NOW())
                RETURNING id
            """)
            hifi_cat_id = cr.fetchone()[0]
            cr.execute(
                "UPDATE product_category SET parent_path = %s WHERE id = %s",
                [f"{hifi_cat_id}/", hifi_cat_id],
            )
            _logger.info("Created HIFI category id=%d", hifi_cat_id)

        # Register xmlid (noupdate=FALSE to match product_category_data.xml)
        cr.execute("""
            INSERT INTO ir_model_data
                (module, name, model, res_id, noupdate,
                 create_uid, write_uid, create_date, write_date)
            VALUES
                ('repair_devices', 'product_category_hifi', 'product.category',
                 %s, FALSE, 1, 1, NOW(), NOW())
            ON CONFLICT (module, name) DO UPDATE SET res_id = EXCLUDED.res_id
        """, [hifi_cat_id])
        _logger.info("Registered xmlid → id=%d", hifi_cat_id)

    # ── Step 2: Migrate repair.device.category → product.category ───────

    if not _table_exists(cr, 'repair_device_category'):
        _logger.info("No repair_device_category table — fresh install, skipping.")
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    cr.execute("""
        SELECT id, name, parent_id, parent_path
        FROM repair_device_category
        ORDER BY parent_path
    """)
    old_cats = cr.fetchall()
    _logger.info("Found %d repair.device.category records to migrate", len(old_cats))

    # Build old→new mapping, create product.category records via ORM
    old_to_new = {}

    for (old_id, name_raw, old_parent_id, _parent_path) in old_cats:
        name_str = _name_from_raw(name_raw)
        new_parent_id = old_to_new.get(old_parent_id, hifi_cat_id)

        # Idempotency: reuse existing category with same name under same parent
        existing = env['product.category'].search([
            ('name', '=', name_str),
            ('parent_id', '=', new_parent_id),
        ], limit=1)

        if existing:
            new_id = existing.id
            _logger.info("  Reusing product.category id=%d for '%s'", new_id, name_str)
        else:
            new_cat = env['product.category'].create({
                'name': name_str,
                'parent_id': new_parent_id,
            })
            new_id = new_cat.id
            _logger.info("  Created product.category id=%d for old id=%d '%s'",
                         new_id, old_id, name_str)

        old_to_new[old_id] = new_id

    # Persist mapping in temp table for use by post-migrate and repair_custom
    cr.execute("""
        CREATE TABLE IF NOT EXISTS _repair_category_migration_map (
            old_id integer PRIMARY KEY,
            new_id integer NOT NULL
        )
    """)
    cr.execute("DELETE FROM _repair_category_migration_map")
    for old_id, new_id in old_to_new.items():
        cr.execute(
            "INSERT INTO _repair_category_migration_map (old_id, new_id) VALUES (%s, %s)",
            [old_id, new_id],
        )
    _logger.info("Stored %d category mappings in temp table", len(old_to_new))

    # ── Step 3: Set product_template.categ_id from mapping ──────────────

    if not _table_exists(cr, 'repair_device'):
        _logger.info("No repair_device table — skipping categ_id assignment.")
        return

    # Products with a mapped category
    cr.execute("""
        UPDATE product_template pt
        SET categ_id = m.new_id
        FROM repair_device rd
        JOIN _repair_category_migration_map m ON m.old_id = rd.category_id
        WHERE pt.id = rd.product_tmpl_id
          AND rd.product_tmpl_id IS NOT NULL
    """)
    _logger.info("Set categ_id via category mapping: %d rows", cr.rowcount)

    # Products without a category mapping → assign to HIFI root
    cr.execute("""
        UPDATE product_template pt
        SET categ_id = %s
        FROM repair_device rd
        WHERE pt.id = rd.product_tmpl_id
          AND rd.product_tmpl_id IS NOT NULL
          AND (rd.category_id IS NULL
               OR rd.category_id NOT IN (SELECT old_id FROM _repair_category_migration_map))
    """, [hifi_cat_id])
    if cr.rowcount:
        _logger.info("Set categ_id to HIFI root for %d unmapped products", cr.rowcount)

    # ── Step 4: Fix product_template.name ───────────────────────────────
    # The old _sync_product_template() stored display_name (brand+model) as
    # product.template.name. Copy the correct model-only name from repair_device.
    # Both columns are JSONB (Odoo 17 translatable fields). We must rebuild the
    # JSONB object with UPPER() applied to each language value.
    cr.execute("""
        UPDATE product_template pt
        SET name = (
            SELECT jsonb_object_agg(key, UPPER(value #>> '{}'))
            FROM jsonb_each(rd.name)
        )
        FROM repair_device rd
        WHERE pt.id = rd.product_tmpl_id
          AND rd.product_tmpl_id IS NOT NULL
          AND rd.name IS NOT NULL
          AND rd.name != '{}'::jsonb
          AND jsonb_typeof(rd.name) = 'object'
    """)
    _logger.info("Fixed product_template.name from repair_device.name (jsonb): %d rows", cr.rowcount)

    # Handle edge case: rd.name stored as plain text (non-JSONB, shouldn't happen but be safe)
    cr.execute("""
        UPDATE product_template pt
        SET name = jsonb_build_object('en_US', UPPER(rd.name::text))
        FROM repair_device rd
        WHERE pt.id = rd.product_tmpl_id
          AND rd.product_tmpl_id IS NOT NULL
          AND rd.name IS NOT NULL
          AND jsonb_typeof(rd.name) != 'object'
    """)
    if cr.rowcount:
        _logger.info("Fixed product_template.name from repair_device.name (text fallback): %d rows", cr.rowcount)

    _logger.info("2.7 pre-migrate: complete.")
