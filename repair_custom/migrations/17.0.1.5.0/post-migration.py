# -*- coding: utf-8 -*-
"""Close any legacy 'Appareil Prêt' activities left over from the old flow."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    act_type = env.ref(
        'repair_custom.mail_act_repair_done', raise_if_not_found=False,
    )
    if not act_type:
        return
    open_activities = env['mail.activity'].search([
        ('activity_type_id', '=', act_type.id),
        ('res_model', '=', 'repair.order'),
    ])
    for act in open_activities:
        try:
            act.action_feedback(
                feedback="Clôture automatique — flux de livraison refondu (sous-projet 3)"
            )
        except Exception:
            act.unlink()
