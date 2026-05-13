# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class RepairOrder(models.Model):
    _inherit = 'repair.order'

    review_sms_state = fields.Selection(
        [
            ('none', 'Non applicable'),
            ('pending', 'En attente'),
            ('sent', 'Envoyé'),
            ('cancelled', 'Annulé'),
            ('skipped', 'Ignoré'),
        ],
        string="SMS d'avis Google",
        default='none',
        tracking=True,
        copy=False,
    )
    review_sms_eligible_date = fields.Datetime(
        string="SMS d'avis - date prévue",
        copy=False,
    )
    review_sms_sent_date = fields.Datetime(
        string="SMS d'avis - date d'envoi",
        copy=False,
    )
    review_sms_skip_reason = fields.Char(
        string="SMS d'avis - motif d'omission",
        copy=False,
    )
