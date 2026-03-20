"""Post-migration for repair_devices 2.6.

Cleans up product names that carry a redundant brand prefix and recomputes
the is_hifi_device stored field after category consolidation.
"""
import re
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def _clean_str(s):
    return re.sub(r'[^a-z0-9]', '', s.lower()) if s else ''


def _strip_brand_prefix(name, brand_clean):
    """Strip brand prefix from name, return uppercased remainder or None."""
    if not _clean_str(name).startswith(brand_clean):
        return None
    target_length = len(brand_clean)
    current_count = 0
    cut_index = 0
    for i, char in enumerate(name):
        if char.isalnum():
            current_count += 1
        if current_count == target_length:
            cut_index = i + 1
            break
    remainder = name[cut_index:].strip()
    remainder = re.sub(r'^[^a-zA-Z0-9]+', '', remainder)
    return remainder.upper() if remainder else None


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    hifi_cat = env.ref('repair_devices.product_category_hifi', raise_if_not_found=False)
    if not hifi_cat:
        _logger.warning("HiFi category not found — skipping post-migration.")
        return

    # Clean up orphan "Appareils Hi-Fi" categories (created by pre-migrate without xmlid)
    cr.execute("""
        DELETE FROM product_category
        WHERE name = 'Appareils Hi-Fi'
          AND id != %s
          AND NOT EXISTS (
              SELECT 1 FROM product_template WHERE categ_id = product_category.id
          )
    """, [hifi_cat.id])
    if cr.rowcount:
        _logger.info("Deleted %d orphan HiFi categories", cr.rowcount)

    templates = env['product.template'].search([('categ_id', 'child_of', hifi_cat.id)])
    _logger.info("Post-migration: processing %d HiFi products for name cleanup", len(templates))

    # First pass: build a map of what each rename would produce, detect collisions
    renames = {}  # tmpl.id -> new_name
    # Track existing names per brand (after rename) to detect duplicates
    brand_names = {}  # brand_id -> set of uppercased names
    for tmpl in templates:
        if not (tmpl.brand_id and tmpl.name):
            continue
        brand_clean = _clean_str(tmpl.brand_id.name)
        if not brand_clean:
            continue
        new_name = _strip_brand_prefix(tmpl.name, brand_clean)
        if new_name and new_name != tmpl.name.upper():
            renames[tmpl.id] = (tmpl, new_name)

    # Build set of names that will exist per brand (from records NOT being renamed)
    for tmpl in templates:
        if tmpl.id not in renames and tmpl.brand_id and tmpl.name:
            brand_names.setdefault(tmpl.brand_id.id, set()).add(tmpl.name.upper())

    # Second pass: apply renames, skipping those that would create duplicates
    cleaned = 0
    skipped = 0
    for tmpl_id, (tmpl, new_name) in renames.items():
        existing = brand_names.setdefault(tmpl.brand_id.id, set())
        if new_name in existing:
            _logger.warning(
                "Skipping rename: '%s' -> '%s' (brand %s) would duplicate",
                tmpl.name, new_name, tmpl.brand_id.name,
            )
            # Keep old name in the existing set so further dupes are also caught
            existing.add(tmpl.name.upper())
            skipped += 1
            continue
        tmpl.name = new_name
        existing.add(new_name)
        cleaned += 1

    _logger.info(
        "Post-migration: cleaned brand prefix from %d product names (%d skipped as duplicates)",
        cleaned, skipped,
    )

    # Recompute is_hifi_device for all products in HiFi category
    templates._compute_is_hifi_device()
    templates.flush_recordset()
    _logger.info("Post-migration: recomputed is_hifi_device for %d records", len(templates))
