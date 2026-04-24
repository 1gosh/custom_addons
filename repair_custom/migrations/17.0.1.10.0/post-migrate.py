# -*- coding: utf-8 -*-
"""Post-migration for 17.0.1.10.0.

Force-rewrite `mail_template_repair_quote_reminder` because the template
lives in a `noupdate="1"` data file. Plain `-u` would not refresh the
model_id / subject / body_html on existing installs, so we push the
module's new XML values over the existing row.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


NEW_SUBJECT = "Rappel : votre devis de réparation {{ object.repair_id.name or object.name }}"

NEW_BODY = """
                <div style="margin: 0px; padding: 0px;">
                    <p>Bonjour <t t-out="object.partner_id.name or ''"/>,</p>
                    <p>
                        Nous vous avons adressé il y a quelques jours un devis pour la réparation de votre
                        <t t-out="(object.repair_id.device_id_name if object.repair_id else False) or 'appareil'"/>
                        (<t t-out="(object.repair_id.name if object.repair_id else object.name) or ''"/>).
                    </p>
                    <p>
                        N'hésitez pas à nous revenir avec votre décision afin que nous puissions planifier
                        les travaux.
                    </p>
                    <p>
                        Vous pouvez consulter et valider le devis directement en ligne :
                        <a t-att-href="object.get_portal_url()">Voir le devis</a>
                    </p>
                    <p>Cordialement,</p>
                    <p><t t-out="user.company_id.name or ''"/></p>
                </div>
"""


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref(
        'repair_custom.mail_template_repair_quote_reminder',
        raise_if_not_found=False,
    )
    if not template:
        _logger.warning(
            "post-migrate 17.0.1.10.0: template mail_template_repair_quote_reminder not found — skipping"
        )
        return
    sale_order_model = env.ref('sale.model_sale_order')
    template.write({
        'model_id': sale_order_model.id,
        'subject': NEW_SUBJECT,
        'body_html': NEW_BODY,
    })
    _logger.info(
        "post-migrate 17.0.1.10.0: rewrote mail_template_repair_quote_reminder to sale.order"
    )
