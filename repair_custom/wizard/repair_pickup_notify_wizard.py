# -*- coding: utf-8 -*-
from odoo import api, fields, models


class RepairPickupNotifyWizard(models.TransientModel):
    _name = 'repair.pickup.notify.wizard'
    _description = "Dossier prêt — notifier le client ?"

    batch_id = fields.Many2one(
        'repair.batch', string="Dossier",
        required=True, readonly=True, ondelete='cascade',
    )
    partner_name = fields.Char(
        related='batch_id.partner_id.name', readonly=True,
    )
    repair_count = fields.Integer(
        related='batch_id.repair_count', readonly=True,
    )

    def action_send(self):
        self.ensure_one()
        self.batch_id.action_notify_client_ready()
        return {'type': 'ir.actions.act_window_close'}

    def action_postpone(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window_close'}
