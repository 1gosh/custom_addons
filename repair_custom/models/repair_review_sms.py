# -*- coding: utf-8 -*-
import logging
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class RepairOrder(models.Model):
    _inherit = 'repair.order'
