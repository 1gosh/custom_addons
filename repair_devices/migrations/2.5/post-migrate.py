"""Post-migration for repair_devices 2.5.

Cleans up product names that carry a redundant brand prefix (e.g. "BANG OLUFSEN
BEOGRAM 3000" → "BEOGRAM 3000") and triggers recomputation of the now-computed
is_hifi_device stored field.
"""
import re
import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def _clean_str(s):
    return re.sub(r'[^a-z0-9]', '', s.lower()) if s else ''


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    hifi_cat = env.ref('repair_devices.product_category_hifi', raise_if_not_found=False)
    if not hifi_cat:
        _logger.warning("HiFi category not found — skipping post-migration.")
        return

    templates = env['product.template'].search([('categ_id', 'child_of', hifi_cat.id)])
    _logger.info("Post-migration: processing %d HiFi products for name cleanup", len(templates))

    cleaned = 0
    for tmpl in templates:
        if not (tmpl.brand_id and tmpl.name):
            continue
        brand_clean = _clean_str(tmpl.brand_id.name)
        if not brand_clean:
            continue
        if not _clean_str(tmpl.name).startswith(brand_clean):
            continue

        # Strip the brand prefix
        input_name = tmpl.name
        target_length = len(brand_clean)
        current_count = 0
        cut_index = 0
        for i, char in enumerate(input_name):
            if char.isalnum():
                current_count += 1
            if current_count == target_length:
                cut_index = i + 1
                break
        remainder = input_name[cut_index:].strip()
        remainder = re.sub(r'^[^a-zA-Z0-9]+', '', remainder)
        if remainder and remainder.upper() != tmpl.name:
            tmpl.name = remainder.upper()
            cleaned += 1

    _logger.info("Post-migration: cleaned brand prefix from %d product names", cleaned)

    # Trigger recompute of the stored computed field
    templates._compute_is_hifi_device()
    templates.flush_recordset()
    _logger.info("Post-migration: recomputed is_hifi_device for %d records", len(templates))
