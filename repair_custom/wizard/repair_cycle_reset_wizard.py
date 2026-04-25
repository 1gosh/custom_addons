# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


SUPPORTED_MODELS = ('repair.order', 'repair.pickup.appointment')


class RepairCycleResetWizard(models.TransientModel):
    _name = 'repair.cycle.reset.wizard'
    _description = "Réinitialiser le cycle de relance"

    res_model_name = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    target_label = fields.Char(compute='_compute_target_label', readonly=True)
    mode = fields.Selection(
        [
            ('reset_only', "Réinitialiser uniquement"),
            ('reset_and_resend', "Réinitialiser ET renvoyer la notification initiale"),
        ],
        default='reset_only',
        required=True,
    )

    @api.depends('res_model_name', 'res_id')
    def _compute_target_label(self):
        for wiz in self:
            if wiz.res_model_name in SUPPORTED_MODELS and wiz.res_id:
                rec = self.env[wiz.res_model_name].browse(wiz.res_id)
                wiz.target_label = rec.display_name if rec.exists() else ''
            else:
                wiz.target_label = ''

    def _get_target(self):
        self.ensure_one()
        if self.res_model_name not in SUPPORTED_MODELS:
            raise UserError(_("Modèle non supporté : %s") % self.res_model_name)
        target = self.env[self.res_model_name].browse(self.res_id)
        if not target.exists():
            raise UserError(_("L'enregistrement cible n'existe plus."))
        return target

    def action_confirm(self):
        self.ensure_one()
        target = self._get_target()
        send = self.mode == 'reset_and_resend'

        if self.res_model_name == 'repair.order':
            target._reset_quote_cycle()
            if send:
                trace = _("Cycle de relance réinitialisé — renvoi de la notification initiale demandé.")
                target.message_post(body=trace)
                if not target.sale_order_id:
                    raise UserError(_(
                        "Aucun devis lié à cette réparation — impossible d'ouvrir le composeur."
                    ))
                return target.sale_order_id.action_quotation_send()
            target.message_post(body=_("Cycle de relance réinitialisé (sans renvoi de mail)."))
            return {'type': 'ir.actions.act_window_close'}

        # repair.pickup.appointment
        target._reset_pickup_cycle(send_initial=send)
        if send:
            target.message_post(body=_(
                "Cycle de relance réinitialisé — notification initiale renvoyée."
            ))
        else:
            target.message_post(body=_(
                "Cycle de relance réinitialisé (sans renvoi de mail)."
            ))
        return {'type': 'ir.actions.act_window_close'}
