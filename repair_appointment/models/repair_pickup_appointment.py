import uuid

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


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
    _order = 'pickup_date desc, id desc'

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
    pickup_date = fields.Date('Date de retrait', tracking=True)
    token = fields.Char(
        required=True, copy=False, readonly=True, index=True,
        default=lambda self: str(uuid.uuid4()),
    )
    notification_sent_at = fields.Datetime('Notification envoyée le')
    last_reminder_sent_at = fields.Datetime('Dernier rappel le')
    contacted = fields.Boolean('Client contacté')
    contacted_at = fields.Datetime('Contacté le')
    escalation_activity_id = fields.Many2one(
        'mail.activity',
        string='Activité "à contacter"',
        compute='_compute_escalation_activity',
        store=True,
    )
    reschedule_count = fields.Integer('Nombre de replanifications', default=0)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company,
    )
    device_count = fields.Integer(
        'Nb. appareils',
        compute='_compute_device_summary',
    )
    device_summary = fields.Char(
        'Appareils',
        compute='_compute_device_summary',
        help="Identification rapide des appareils à préparer pour le retrait.",
    )
    _sql_constraints = [
        ('token_unique', 'UNIQUE(token)', "Jeton déjà utilisé."),
    ]

    @api.depends(
        'repair_ids',
        'repair_ids.device_id_name',
    )
    def _compute_device_summary(self):
        for apt in self:
            repairs = apt.repair_ids
            apt.device_count = len(repairs)
            if not repairs:
                apt.device_summary = _("Aucun appareil")
                continue
            parts = [
                repair.device_id_name or _("Appareil")
                for repair in repairs
            ]
            apt.device_summary = ", ".join(parts)

    @api.depends('name', 'partner_id')
    def _compute_display_name(self):
        """Show the customer name as the record label — this is what
        Odoo's calendar view, many2one dropdowns and breadcrumbs render.
        The sequence ref (self.name) stays visible in form/tree views."""
        for apt in self:
            apt.display_name = apt.partner_id.name or apt.name or ''

    @api.constrains('state', 'pickup_date')
    def _check_scheduled_has_date(self):
        for apt in self:
            if apt.state == 'scheduled' and not apt.pickup_date:
                raise ValidationError(_(
                    "Un rendez-vous confirmé doit avoir une date de retrait."
                ))

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

    def write(self, vals):
        track_date_change = False
        old_dates = {}
        if 'pickup_date' in vals and not self.env.context.get('skip_reschedule_notification'):
            track_date_change = True
            old_dates = {apt.id: apt.pickup_date for apt in self}
        res = super().write(vals)
        if track_date_change:
            template = self.env.ref(
                'repair_appointment.mail_template_pickup_reschedule',
                raise_if_not_found=False,
            )
            for apt in self:
                if (apt.state == 'scheduled'
                        and old_dates.get(apt.id)
                        and old_dates.get(apt.id) != apt.pickup_date):
                    if template:
                        template.send_mail(apt.id, force_send=False)
                    apt.message_post(body=_(
                        "RDV déplacé — notification client envoyée."
                    ))
        return res

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

    def action_schedule(self, pickup_date):
        """Transition pending → scheduled, or update pickup_date in place
        on an already-scheduled appointment. Validates day availability
        unless context `skip_slot_validation` is True."""
        for apt in self:
            apt._ensure_not_terminal()
            if apt.state not in ('pending', 'scheduled'):
                raise UserError(_("Impossible de planifier ce rendez-vous."))

            if not self.env.context.get('skip_slot_validation'):
                apt._validate_day(pickup_date)

            was_scheduled = apt.state == 'scheduled'
            old_date = apt.pickup_date

            apt.write({
                'pickup_date': pickup_date,
                'state': 'scheduled',
            })

            if was_scheduled and old_date != pickup_date:
                apt.reschedule_count += 1
                apt.message_post(body=_(
                    "RDV déplacé du %(old)s au %(new)s."
                ) % {'old': old_date, 'new': pickup_date})
            elif not was_scheduled:
                apt.message_post(body=_(
                    "RDV confirmé pour le %s."
                ) % pickup_date)

            apt._close_open_escalation_activities()

    def action_confirm_manual(self):
        """Manual confirmation path for appointments booked by phone.
        Pickup date must already be filled on the record; slot validation
        is bypassed so staff can override closure / capacity rules."""
        for apt in self:
            if apt.state != 'pending':
                raise UserError(_(
                    "Seuls les rendez-vous en attente peuvent être confirmés "
                    "manuellement."
                ))
            if not apt.pickup_date:
                raise UserError(_(
                    "Renseignez la date de retrait avant de confirmer."
                ))
            apt.with_context(skip_slot_validation=True).action_schedule(
                apt.pickup_date,
            )

    def _validate_day(self, pickup_date):
        if not pickup_date:
            raise UserError(_("Date de retrait requise."))
        if not self._is_day_available(pickup_date):
            raise UserError(_("Ce jour n'est plus disponible."))

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
    def _compute_available_days(self, location, date_from=None, date_to=None,
                                booking_horizon_days=None):
        """Return a list of dicts describing each calendar day in the
        booking window at `location`.

        Each dict: {
            'date': date,
            'state': 'open' | 'closed' | 'full' | 'lead_time',
            'remaining_capacity': int,
        }

        Days before the min-lead cutoff are excluded entirely (the portal
        renders the cutoff explicitly; no need to send them). Days past
        the horizon are also excluded. Days within the window are always
        returned, with their state explaining why they are/aren't bookable.
        """
        from datetime import timedelta

        today = fields.Date.today()
        min_lead = self._get_min_lead_days()
        horizon = (booking_horizon_days
                   if booking_horizon_days is not None
                   else self._get_booking_horizon_days())

        earliest = today + timedelta(days=min_lead)
        latest = today + timedelta(days=horizon)

        date_from = max(date_from or earliest, earliest)
        date_to = min(date_to or latest, latest)

        if date_from > date_to:
            return []

        schedule = self.env['repair.pickup.schedule'].search(
            [('location_id', '=', location.id), ('active', '=', True)],
            limit=1,
        )
        if not schedule:
            return []

        closures = self.env['repair.pickup.closure'].search(
            [('active', '=', True)],
        ).filtered(
            lambda c: c.location_id in (location, False) or c.location_id.id is False
        )

        results = []
        day = date_from
        while day <= date_to:
            entry = {'date': day, 'state': 'open', 'remaining_capacity': 0}
            if not schedule._day_is_open(day.weekday()):
                entry['state'] = 'closed'
            elif any(c._covers(day, location) for c in closures):
                entry['state'] = 'closed'
            else:
                booked = self._count_booked_on_day(day, location)
                remaining = max(0, schedule.daily_capacity - booked)
                entry['remaining_capacity'] = remaining
                entry['state'] = 'open' if remaining > 0 else 'full'
            results.append(entry)
            day += timedelta(days=1)

        return results

    @api.model
    def _count_booked_on_day(self, pickup_date, location):
        """Count scheduled appointments at `location` on `pickup_date`."""
        return self.search_count([
            ('pickup_date', '=', pickup_date),
            ('location_id', '=', location.id),
            ('state', '=', 'scheduled'),
        ])

    def _is_day_available(self, pickup_date):
        """True if the target day has remaining capacity and is within
        the schedule + closures + lead-time rules. Excludes self from
        the count so same-day reschedules pass."""
        self.ensure_one()
        from datetime import timedelta
        if not self.location_id or not pickup_date:
            return False
        schedule = self.env['repair.pickup.schedule'].search(
            [('location_id', '=', self.location_id.id)], limit=1,
        )
        if not schedule:
            return False
        if not schedule._day_is_open(pickup_date.weekday()):
            return False
        closures = self.env['repair.pickup.closure'].search([('active', '=', True)])
        for c in closures:
            if c._covers(pickup_date, self.location_id):
                return False
        min_lead = self._get_min_lead_days()
        if pickup_date < fields.Date.today() + timedelta(days=min_lead):
            if not self.env.context.get('bypass_lead_time'):
                return False
        booked = self.search_count([
            ('pickup_date', '=', pickup_date),
            ('location_id', '=', self.location_id.id),
            ('state', '=', 'scheduled'),
            ('id', '!=', self.id),
        ])
        if booked >= schedule.daily_capacity:
            if not self.env.context.get('bypass_capacity'):
                return False
        return True

    # ------------------------------------------------------------------
    # Escalation activity handling
    # ------------------------------------------------------------------

    @api.depends('activity_ids', 'activity_ids.activity_type_id')
    def _compute_escalation_activity(self):
        activity_type = self.env.ref(
            'repair_appointment.activity_pickup_to_contact',
            raise_if_not_found=False,
        )
        for apt in self:
            if not activity_type:
                apt.escalation_activity_id = False
                continue
            matching = apt.activity_ids.filtered(
                lambda a: a.activity_type_id == activity_type
            )
            apt.escalation_activity_id = matching[:1]

    def _create_escalation_activity(self):
        """Create one activity per user in group_repair_manager."""
        self.ensure_one()
        activity_type = self.env.ref('repair_appointment.activity_pickup_to_contact')
        managers = self.env.ref('repair_custom.group_repair_manager').users
        note_tmpl = _(
            "Le client %(name)s n'a pas pris rendez-vous pour récupérer son appareil.\n"
            "Dossier : %(batch)s\nTéléphone : %(phone)s"
        )
        for manager in managers:
            self.env['mail.activity'].create({
                'res_model_id': self.env['ir.model']._get_id('repair.pickup.appointment'),
                'res_id': self.id,
                'activity_type_id': activity_type.id,
                'user_id': manager.id,
                'summary': _("Client à contacter — RDV retrait non pris"),
                'note': note_tmpl % {
                    'name': self.partner_id.name or '',
                    'batch': self.batch_id.name or '',
                    'phone': self.partner_id.phone or '',
                },
            })

    def action_mark_contacted(self):
        """Manager clicked 'Contacté'. Marks all sibling activities (of
        type activity_pickup_to_contact) on this record as done and
        sets the `contacted` flag so the CRON restarts from contacted_at."""
        activity_type = self.env.ref('repair_appointment.activity_pickup_to_contact')
        for apt in self:
            activities = self.env['mail.activity'].search([
                ('res_model', '=', 'repair.pickup.appointment'),
                ('res_id', '=', apt.id),
                ('activity_type_id', '=', activity_type.id),
            ])
            for act in activities:
                act.action_feedback(feedback=_("Marqué contacté depuis le RDV."))
            apt.write({
                'contacted': True,
                'contacted_at': fields.Datetime.now(),
            })
            apt.message_post(body=_("Client marqué comme contacté."))

    def _close_open_escalation_activities(self):
        """Called from action_schedule. Cleanly closes any open
        escalation activities on the record."""
        activity_type = self.env.ref(
            'repair_appointment.activity_pickup_to_contact',
            raise_if_not_found=False,
        )
        if not activity_type:
            return
        for apt in self:
            activities = self.env['mail.activity'].search([
                ('res_model', '=', 'repair.pickup.appointment'),
                ('res_id', '=', apt.id),
                ('activity_type_id', '=', activity_type.id),
            ])
            for act in activities:
                act.action_feedback(feedback=_("RDV planifié — escalade close."))

    # ------------------------------------------------------------------
    # Mail / portal helpers
    # ------------------------------------------------------------------

    def _portal_url(self):
        """Absolute URL to the client portal for this appointment."""
        self.ensure_one()
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return f"{base.rstrip('/')}/my/pickup/{self.token}"

    def _send_reminder_mail(self):
        self.ensure_one()
        template = self.env.ref(
            'repair_appointment.mail_template_pickup_reminder',
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(self.id, force_send=False)

    def action_send_reminder_now(self):
        for apt in self:
            apt._send_reminder_mail()
            apt.last_reminder_sent_at = fields.Datetime.now()
            apt.message_post(body=_("Rappel envoyé manuellement."))

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------

    def action_open_batch(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.batch_id.display_name or _("Dossier"),
            'res_model': 'repair.batch',
            'res_id': self.batch_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Reminder CRON
    # ------------------------------------------------------------------

    def _get_reminder_delay_days(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_appointment.reminder_delay_days', default='3',
        ))

    def _get_escalation_delay_days(self):
        return int(self.env['ir.config_parameter'].sudo().get_param(
            'repair_appointment.escalation_delay_days', default='3',
        ))

    @api.model
    def _cron_process_pending_appointments(self):
        """Send the single reminder and create escalation activities.
        Pseudocode matches the spec's Reminder CRON section."""
        from datetime import timedelta
        now = fields.Datetime.now()
        reminder_delay = self._get_reminder_delay_days()
        escalation_delay = self._get_escalation_delay_days()

        pending = self.search([
            ('state', '=', 'pending'),
            ('notification_sent_at', '!=', False),
        ])

        for apt in pending:
            # Phase 1: single reminder mail
            if (not apt.last_reminder_sent_at
                    and not apt.contacted
                    and now >= apt.notification_sent_at + timedelta(days=reminder_delay)):
                apt._send_reminder_mail()
                apt.last_reminder_sent_at = now
                continue

            # Phase 2: escalation
            apt.invalidate_recordset(['escalation_activity_id'])
            if apt.escalation_activity_id:
                continue  # still open, wait for manager

            if apt.contacted:
                if (apt.contacted_at
                        and now >= apt.contacted_at + timedelta(days=escalation_delay)):
                    apt._create_escalation_activity()
                    apt.contacted = False  # consume the flag
            elif apt.last_reminder_sent_at:
                if now >= apt.last_reminder_sent_at + timedelta(days=escalation_delay):
                    apt._create_escalation_activity()
