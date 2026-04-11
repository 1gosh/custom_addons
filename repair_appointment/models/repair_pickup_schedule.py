from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class RepairPickupSchedule(models.Model):
    _name = 'repair.pickup.schedule'
    _description = 'Horaires de retrait par lieu'
    _rec_name = 'location_id'

    location_id = fields.Many2one(
        'repair.pickup.location',
        string='Lieu de retrait',
        required=True,
        ondelete='cascade',
    )
    active = fields.Boolean(default=True)

    monday_open = fields.Boolean('Lundi', default=True)
    tuesday_open = fields.Boolean('Mardi', default=True)
    wednesday_open = fields.Boolean('Mercredi', default=True)
    thursday_open = fields.Boolean('Jeudi', default=True)
    friday_open = fields.Boolean('Vendredi', default=True)
    saturday_open = fields.Boolean('Samedi', default=True)
    sunday_open = fields.Boolean('Dimanche', default=False)

    slot1_start = fields.Float('Créneau 1 début', default=15.0)
    slot1_end = fields.Float('Créneau 1 fin', default=17.25)
    slot2_start = fields.Float('Créneau 2 début', default=17.25)
    slot2_end = fields.Float('Créneau 2 fin', default=19.5)

    slot_capacity = fields.Integer(
        'Capacité par créneau',
        default=3,
        help="Nombre maximum de rendez-vous simultanés dans un créneau.",
    )

    _sql_constraints = [
        ('location_unique',
         'UNIQUE(location_id)',
         "Il existe déjà un horaire pour ce lieu."),
    ]

    @api.constrains('slot1_start', 'slot1_end', 'slot2_start', 'slot2_end')
    def _check_slot_ranges(self):
        for rec in self:
            if rec.slot1_end <= rec.slot1_start:
                raise ValidationError(_("Le créneau 1 doit se terminer après son début."))
            if rec.slot2_end <= rec.slot2_start:
                raise ValidationError(_("Le créneau 2 doit se terminer après son début."))
            if rec.slot2_start < rec.slot1_end:
                raise ValidationError(_("Le créneau 2 doit commencer après la fin du créneau 1."))

    @api.constrains('slot_capacity')
    def _check_slot_capacity(self):
        for rec in self:
            if rec.slot_capacity < 1:
                raise ValidationError(_("La capacité doit être au moins 1."))

    def _day_is_open(self, weekday_index):
        """weekday_index: 0=Mon..6=Sun. Returns bool."""
        mapping = [
            self.monday_open, self.tuesday_open, self.wednesday_open,
            self.thursday_open, self.friday_open, self.saturday_open, self.sunday_open,
        ]
        return bool(mapping[weekday_index])

    @api.model
    def _seed_default_schedules(self):
        """Create a default Mon–Sat schedule for every location
        that doesn't already have one."""
        Location = self.env['repair.pickup.location']
        for location in Location.search([]):
            if not self.search([('location_id', '=', location.id)], limit=1):
                self.create({'location_id': location.id})
