import uuid

from odoo import api, fields, models, _
from odoo.exceptions import UserError


STATE_SELECTION = [
    ('pending', 'En attente de créneau'),
    ('scheduled', 'Confirmé'),
    ('done', 'Terminé'),
    ('no_show', 'Absent'),
    ('cancelled', 'Annulé'),
]


class RepairPickupAppointment(models.Model):
    _name = 'repair.pickup.appointment'
    _description = 'Rendez-vous de retrait'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start_datetime desc, id desc'

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: _('Nouveau'),
    )
    batch_id = fields.Many2one(
        'repair.batch',
        string='Dossier de dépôt',
        required=True,
        ondelete='restrict',
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        related='batch_id.partner_id',
        store=True,
        readonly=True,
    )
    repair_ids = fields.One2many(
        'repair.order',
        related='batch_id.repair_ids',
        readonly=True,
    )
    location_id = fields.Many2one(
        'repair.pickup.location',
        string='Lieu de retrait',
        compute='_compute_location_id',
        store=True,
        readonly=True,
    )
    state = fields.Selection(
        STATE_SELECTION,
        default='pending',
        required=True,
        tracking=True,
    )
    start_datetime = fields.Datetime('Début', tracking=True)
    end_datetime = fields.Datetime('Fin', tracking=True)
    token = fields.Char(
        required=True, copy=False, readonly=True, index=True,
        default=lambda self: str(uuid.uuid4()),
    )
    notification_sent_at = fields.Datetime('Notification envoyée le')
    last_reminder_sent_at = fields.Datetime('Dernier rappel le')
    contacted = fields.Boolean('Client contacté')
    contacted_at = fields.Datetime('Contacté le')
    reschedule_count = fields.Integer('Nombre de replanifications', default=0)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company,
    )

    _sql_constraints = [
        ('token_unique', 'UNIQUE(token)', "Jeton déjà utilisé."),
    ]

    @api.depends('batch_id.repair_ids.pickup_location_id')
    def _compute_location_id(self):
        for apt in self:
            loc = False
            for repair in apt.batch_id.repair_ids:
                if repair.pickup_location_id:
                    loc = repair.pickup_location_id
                    break
            if not loc:
                loc = self.env['repair.pickup.location'].search(
                    [('company_id', 'in', [apt.company_id.id, False])], limit=1,
                )
            apt.location_id = loc

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nouveau')) == _('Nouveau'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'repair.pickup.appointment'
                ) or _('Nouveau')
            if not vals.get('token'):
                vals['token'] = str(uuid.uuid4())
        return super().create(vals_list)
