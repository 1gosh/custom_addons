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
        if not start_datetime or not end_datetime:
            raise UserError(_("Début et fin de créneau requis."))
        if end_datetime <= start_datetime:
            raise UserError(_("La fin doit être postérieure au début."))
        if not self._is_slot_available(start_datetime, end_datetime):
            raise UserError(_("Ce créneau n'est plus disponible."))

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

    # ------------------------------------------------------------------
    # Slot availability helpers
    # ------------------------------------------------------------------

    def _get_booking_horizon_days(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_appointment.booking_horizon_days', default='14',
        ))

    def _get_min_lead_days(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_appointment.min_lead_days', default='2',
        ))

    @api.model
    def _compute_available_slots(self, location, date_from=None, date_to=None,
                                 booking_horizon_days=None):
        """Return a list of dicts describing available slots between
        date_from and date_to at `location`.

        Each dict: {
            'datetime_start': datetime,
            'datetime_end': datetime,
            'remaining_capacity': int,
        }

        Respects: schedule weekly mask, closures, min lead time,
        booking horizon, slot capacity.
        """
        from datetime import timedelta

        today = fields.Date.today()
        min_lead = self._get_min_lead_days()
        horizon = booking_horizon_days if booking_horizon_days is not None \
            else self._get_booking_horizon_days()

        earliest = today + timedelta(days=min_lead)
        latest = today + timedelta(days=horizon)

        date_from = max(date_from or earliest, earliest)
        date_to = min(date_to or latest, latest)

        if date_from > date_to:
            return []

        schedule = self.env['repair.pickup.schedule'].search(
            [('location_id', '=', location.id), ('active', '=', True)], limit=1,
        )
        if not schedule:
            return []

        closures = self.env['repair.pickup.closure'].search(
            [('active', '=', True)],
        ).filtered(lambda c: c.location_id in (location, False) or c.location_id.id is False)

        slots = []
        day = date_from
        while day <= date_to:
            if schedule._day_is_open(day.weekday()) and not any(
                c._covers(day, location) for c in closures
            ):
                for (start_f, end_f) in [
                    (schedule.slot1_start, schedule.slot1_end),
                    (schedule.slot2_start, schedule.slot2_end),
                ]:
                    start_dt = self._float_to_datetime(day, start_f)
                    end_dt = self._float_to_datetime(day, end_f)
                    booked = self._count_booked_in_slot(start_dt, location)
                    remaining = max(0, schedule.slot_capacity - booked)
                    slots.append({
                        'datetime_start': start_dt,
                        'datetime_end': end_dt,
                        'remaining_capacity': remaining,
                    })
            day += timedelta(days=1)

        return slots

    @api.model
    def _float_to_datetime(self, day, float_hour):
        """Convert (date, 15.25) → datetime(day, 15, 15, 0)."""
        from datetime import datetime as dt_cls
        hours = int(float_hour)
        minutes = int(round((float_hour - hours) * 60))
        return dt_cls.combine(day, dt_cls.min.time()).replace(hour=hours, minute=minutes)

    @api.model
    def _count_booked_in_slot(self, start_dt, location):
        """Count scheduled appointments whose start_datetime matches
        start_dt and whose location is `location`. Excludes cancelled,
        done, no_show."""
        return self.search_count([
            ('start_datetime', '=', start_dt),
            ('location_id', '=', location.id),
            ('state', '=', 'scheduled'),
        ])

    def _is_slot_available(self, start_dt, end_dt):
        """True if the target slot has remaining capacity and is within
        the schedule + closures + lead-time rules. Excludes self from
        the count so reschedules into the same slot work."""
        self.ensure_one()
        if not self.location_id:
            return False
        schedule = self.env['repair.pickup.schedule'].search(
            [('location_id', '=', self.location_id.id)], limit=1,
        )
        if not schedule:
            return False
        if not schedule._day_is_open(start_dt.weekday()):
            return False
        closures = self.env['repair.pickup.closure'].search([('active', '=', True)])
        for c in closures:
            if c._covers(start_dt.date(), self.location_id):
                return False
        from datetime import timedelta
        min_lead = self._get_min_lead_days()
        if start_dt.date() < fields.Date.today() + timedelta(days=min_lead):
            # Context bypass for staff
            if not self.env.context.get('bypass_lead_time'):
                return False
        booked = self.search_count([
            ('start_datetime', '=', start_dt),
            ('location_id', '=', self.location_id.id),
            ('state', '=', 'scheduled'),
            ('id', '!=', self.id),
        ])
        if booked >= schedule.slot_capacity:
            if not self.env.context.get('bypass_capacity'):
                return False
        return True
