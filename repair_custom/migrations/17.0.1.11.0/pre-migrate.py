# -*- coding: utf-8 -*-
"""Flip noupdate=False on overridden mail templates so our XML data block
(`<data noupdate="0">` in data/mail_templates.xml) can actually update them on
subsequent module upgrades. Without this, odoo/models.py::_load_records (line
~5086) skips the update because the existing ir.model.data row's noupdate flag
is still True from the originating module.
"""


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_model_data
           SET noupdate = FALSE
         WHERE module = 'sale'
           AND name   = 'email_template_edi_sale'
        """
    )
