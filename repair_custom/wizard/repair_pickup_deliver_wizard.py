# -*- coding: utf-8 -*-
from odoo import api, fields, models


class RepairPickupDeliverWizard(models.TransientModel):
    _name = 'repair.pickup.deliver.wizard'
    _description = "Marquer la réparation comme livrée ?"

    batch_id = fields.Many2one(
        'repair.batch', string="Dossier",
        required=True, readonly=True, ondelete='cascade',
    )
    invoice_id = fields.Many2one(
        'account.move', string="Facture",
        required=True, readonly=True, ondelete='cascade',
    )
    partner_name = fields.Char(
        related='batch_id.partner_id.name', readonly=True,
    )
    repair_ids = fields.Many2many(
        'repair.order', string="Réparations concernées",
        compute='_compute_repair_ids',
    )

    @api.depends('batch_id')
    def _compute_repair_ids(self):
        for wiz in self:
            wiz.repair_ids = wiz.batch_id.repair_ids.filtered(
                lambda r: r.delivery_state == 'none'
                and r.state in ('done', 'irreparable')
            )

    def action_confirm(self):
        self.ensure_one()
        self.batch_id.action_mark_delivered()
        return {'type': 'ir.actions.act_window_close'}

    def action_dismiss(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window_close'}
