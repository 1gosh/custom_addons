# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, models

CUSTOM_LAYOUT = 'repair_custom.mail_notification_layout'
NATIVE_LAYOUT = 'mail.mail_notification_layout_with_responsible_signature'


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_quotation_send(self):
        action = super().action_quotation_send()
        if isinstance(action, dict) and action.get('context'):
            action['context'] = dict(
                action['context'],
                default_email_layout_xmlid=CUSTOM_LAYOUT,
            )
        return action

    def _send_order_notification_mail(self, mail_template):
        """Mirrors sale.order._send_order_notification_mail (sale_order.py:1036)
        but routes through our notification layout."""
        self.ensure_one()
        if not mail_template:
            return
        if self.env.su:
            self = self.with_user(SUPERUSER_ID)
        self.with_context(force_send=True).message_post_with_source(
            mail_template,
            email_layout_xmlid=CUSTOM_LAYOUT,
            subtype_xmlid='mail.mt_comment',
        )

    def action_cancel(self):
        action = super().action_cancel()
        if isinstance(action, dict) and action.get('context', {}).get(
                'default_email_layout_xmlid') == NATIVE_LAYOUT:
            action['context'] = dict(
                action['context'],
                default_email_layout_xmlid=CUSTOM_LAYOUT,
            )
        return action
