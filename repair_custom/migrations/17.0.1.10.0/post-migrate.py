# -*- coding: utf-8 -*-
"""Post-migration for 17.0.1.10.0.

Force-rewrite `mail_template_repair_quote_reminder` because the template
is in a `noupdate="1"` data file and a plain `-u` would not refresh the
`model_id` / `subject` / `body_html` on existing installs.

The actual rewrite is applied by re-reading the loaded template record
after the XML has been reloaded and pushing its current XML-declared
values onto any existing row.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Populated in Task 5 once the new template XML is authoritative.
    _logger.info("post-migrate 17.0.1.10.0: placeholder — template rewrite pending")
