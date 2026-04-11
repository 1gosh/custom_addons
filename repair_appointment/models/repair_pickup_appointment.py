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

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    TERMINAL_STATES = ('done', 'no_show', 'cancelled')

    def _ensure_not_terminal(self):
        for apt in self:
            if apt.state in self.TERMINAL_STATES:
                raise UserError(_(
                    "Ce rendez-vous est déjà dans un état final (%s).",
                ) % dict(STATE_SELECTION).get(apt.state))

    def action_schedule(self, start_datetime, end_datetime):
        """Transition pending → scheduled, or update datetime in-place on
        an already-scheduled appointment. Validates slot availability
        unless context `skip_slot_validation` is True."""
        for apt in self:
            apt._ensure_not_terminal()
            if apt.state not in ('pending', 'scheduled'):
                raise UserError(_("Impossible de planifier ce rendez-vous."))

            if not self.env.context.get('skip_slot_validation'):
                apt._validate_slot(start_datetime, end_datetime)

            was_scheduled = apt.state == 'scheduled'
            old_start = apt.start_datetime

            apt.write({
                'start_datetime': start_datetime,
                'end_datetime': end_datetime,
                'state': 'scheduled',
            })

            if was_scheduled and old_start != start_datetime:
                apt.reschedule_count += 1
                apt.message_post(body=_(
                    "RDV déplacé du %(old)s au %(new)s."
                ) % {
                    'old': old_start,
                    'new': start_datetime,
                })
            elif not was_scheduled:
                apt.message_post(body=_(
                    "RDV confirmé pour le %s."
                ) % start_datetime)

            # Mark any open escalation activities as done
            apt._close_open_escalation_activities()

    def _validate_slot(self, start_datetime, end_datetime):
        """Placeholder — full validation lives in Task 7."""
        if not start_datetime or not end_datetime:
            raise UserError(_("Début et fin de créneau requis."))
        if end_datetime <= start_datetime:
            raise UserError(_("La fin doit être postérieure au début."))

    def _close_open_escalation_activities(self):
        """Placeholder — full escalation handling lives in Task 10."""
        return

    def action_mark_done(self):
        for apt in self:
            if apt.state != 'scheduled':
                raise UserError(_(
                    "Seuls les rendez-vous confirmés peuvent être marqués comme terminés."
                ))
            apt.state = 'done'
            apt.message_post(body=_("Rendez-vous terminé."))

    def action_mark_no_show(self):
        for apt in self:
            if apt.state != 'scheduled':
                raise UserError(_(
                    "Seuls les rendez-vous confirmés peuvent être marqués comme absents."
                ))
            apt.state = 'no_show'
            apt.message_post(body=_("Client absent."))

    def action_cancel(self):
        for apt in self:
            apt._ensure_not_terminal()
            apt.state = 'cancelled'
            apt.message_post(body=_("Rendez-vous annulé."))
