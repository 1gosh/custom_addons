"""Post-migration for repair_devices 2.7.

Runs AFTER schema update + data loading. New columns exist on product.template
(brand_id, is_hifi_device, production_year) and stock.lot (is_hifi_unit,
hifi_partner_id, hifi_notes, hifi_variant_id, hifi_image).

Steps:
  1. Copy brand_id, production_year from repair_device → product_template
  2. Set tracking/type/sale_ok on HiFi products
  3. Migrate repair.device.unit → stock.lot (create new lots)
  4. Migrate M2M variants (repair_device_variant_rel → product_template_variant_rel)
  5. Drop FK on repair_order.category_id and remap values (prep for repair_custom)
  6. Recompute is_hifi_device
  7. Archive old tables
"""
import logging

from odoo import api, SUPERUSER_ID

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
    env = api.Environment(cr, SUPERUSER_ID, {})

    _logger.info("2.7 post-migrate: starting")

    if not _table_exists(cr, 'repair_device'):
        _logger.info("No repair_device table — fresh install, skipping.")
        return

    # ── Step 1: Copy brand_id, production_year → product_template ────────

    cr.execute("""
        UPDATE product_template pt
        SET brand_id = rd.brand_id,
            production_year = rd.production_year
        FROM repair_device rd
        WHERE pt.id = rd.product_tmpl_id
          AND rd.product_tmpl_id IS NOT NULL
    """)
    _logger.info("Step 1: copied brand_id/production_year: %d rows", cr.rowcount)

    # ── Step 2: Set tracking/type/sale_ok ────────────────────────────────

    cr.execute("""
        UPDATE product_template
        SET tracking = 'serial', type = 'product', sale_ok = TRUE
        WHERE brand_id IS NOT NULL
          AND (tracking != 'serial' OR type != 'product' OR sale_ok != TRUE)
    """)
    _logger.info("Step 2: enforced tracking/type/sale_ok: %d rows", cr.rowcount)

    # ── Step 3: Migrate repair.device.unit → stock.lot ───────────────────
    # At fc635ea, repair_device_unit has NO lot_id column — all units need
    # new stock.lot records.

    if not _table_exists(cr, 'repair_device_unit'):
        _logger.info("No repair_device_unit table — skipping unit migration.")
    else:
        cr.execute("""
            SELECT rdu.id, rdu.serial_number, rdu.partner_id, rdu.notes,
                   rdu.variant_id,
                   pp.product_id, rd.product_tmpl_id,
                   (SELECT id FROM res_company LIMIT 1) as company_id
            FROM repair_device_unit rdu
            JOIN repair_device rd ON rd.id = rdu.device_id
            JOIN product_template pt ON pt.id = rd.product_tmpl_id
            CROSS JOIN LATERAL (
                SELECT pp.id as product_id FROM product_product pp
                WHERE pp.product_tmpl_id = pt.id AND pp.active = TRUE
                ORDER BY pp.id LIMIT 1
            ) pp
            WHERE rd.product_tmpl_id IS NOT NULL
        """)
        units = cr.dictfetchall()
        _logger.info("Step 3: found %d units to migrate to stock.lot", len(units))

        # Create temp mapping table for cross-module use
        cr.execute("DROP TABLE IF EXISTS _repair_unit_lot_map")
        cr.execute("""
            CREATE TABLE _repair_unit_lot_map (
                unit_id integer PRIMARY KEY,
                lot_id integer NOT NULL
            )
        """)

        migr_serial_counter = 0
        for unit in units:
            if unit['serial_number']:
                serial = unit['serial_number']
            else:
                migr_serial_counter += 1
                serial = f"HF/MIGR/{migr_serial_counter:04d}"

            # Idempotency: check if lot already exists for this product+serial
            cr.execute("""
                SELECT id FROM stock_lot
                WHERE name = %s AND product_id = %s AND company_id = %s
                LIMIT 1
            """, [serial, unit['product_id'], unit['company_id']])
            existing = cr.fetchone()

            if existing:
                lot_id = existing[0]
                # Update HiFi fields on existing lot
                cr.execute("""
                    UPDATE stock_lot
                    SET is_hifi_unit = TRUE,
                        hifi_partner_id = %s,
                        hifi_notes = %s,
                        hifi_variant_id = %s
                    WHERE id = %s
                """, [unit['partner_id'], unit['notes'],
                      unit['variant_id'], lot_id])
            else:
                cr.execute("""
                    INSERT INTO stock_lot
                        (name, product_id, company_id, is_hifi_unit,
                         hifi_partner_id, hifi_notes, hifi_variant_id,
                         create_uid, create_date, write_uid, write_date)
                    VALUES (%s, %s, %s, TRUE, %s, %s, %s,
                            1, NOW(), 1, NOW())
                    RETURNING id
                """, [serial, unit['product_id'], unit['company_id'],
                      unit['partner_id'], unit['notes'],
                      unit['variant_id']])
                lot_id = cr.fetchone()[0]

            cr.execute(
                "INSERT INTO _repair_unit_lot_map (unit_id, lot_id) VALUES (%s, %s)",
                [unit['id'], lot_id],
            )

        _logger.info("Step 3: created/mapped %d stock.lot records", len(units))

    # ── Step 4: Migrate M2M variants ─────────────────────────────────────

    if _table_exists(cr, 'repair_device_variant_rel'):
        cr.execute("""
            INSERT INTO product_template_variant_rel
                (product_template_id, repair_device_variant_id)
            SELECT rd.product_tmpl_id, rdvr.variant_id
            FROM repair_device_variant_rel rdvr
            JOIN repair_device rd ON rd.id = rdvr.device_id
            WHERE rd.product_tmpl_id IS NOT NULL
            ON CONFLICT DO NOTHING
        """)
        _logger.info("Step 4: migrated variant M2M: %d rows", cr.rowcount)

    # ── Step 5: Drop FK on repair_order.category_id + remap values ───────
    # repair_order.category_id currently FK → repair_device_category.
    # After repair_custom schema update, it will FK → product_category.
    # We must remap values NOW so the new FK doesn't fail.

    if _table_exists(cr, 'repair_order') and _column_exists(cr, 'repair_order', 'category_id'):
        _drop_fk_constraints(cr, 'repair_order', 'category_id')

        if _table_exists(cr, '_repair_category_migration_map'):
            cr.execute("""
                UPDATE repair_order ro
                SET category_id = m.new_id
                FROM _repair_category_migration_map m
                WHERE ro.category_id = m.old_id
            """)
            _logger.info("Step 5: remapped repair_order.category_id: %d rows", cr.rowcount)

            # Set unmapped to NULL (safer than pointing at wrong record)
            cr.execute("""
                UPDATE repair_order
                SET category_id = NULL
                WHERE category_id IS NOT NULL
                  AND category_id NOT IN (SELECT id FROM product_category)
            """)
            if cr.rowcount:
                _logger.info("Step 5: nulled %d unmapped category_id values", cr.rowcount)

    # ── Step 6: Recompute is_hifi_device ─────────────────────────────────

    templates = env['product.template'].search([('brand_id', '!=', False)])
    if templates:
        templates._compute_is_hifi_device()
        templates.flush_recordset()
        _logger.info("Step 6: recomputed is_hifi_device for %d templates", len(templates))

    # ── Step 7: Archive old tables ───────────────────────────────────────
    # Keep temp mapping tables for repair_custom migration.

    for old_table in ['repair_device_category', 'repair_device', 'repair_device_unit']:
        if _table_exists(cr, old_table):
            archived = f"_archived_{old_table}"
            if _table_exists(cr, archived):
                cr.execute(f'DROP TABLE "{archived}" CASCADE')
            cr.execute(f'ALTER TABLE "{old_table}" RENAME TO "{archived}"')
            _logger.info("Step 7: archived %s → %s", old_table, archived)

    _logger.info("2.7 post-migrate: complete.")
