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

    daily_capacity = fields.Integer(
        'Capacité par jour',
        default=6,
        help="Nombre maximum de retraits acceptés pour un jour ouvré.",
    )

    _sql_constraints = [
        ('location_unique',
         'UNIQUE(location_id)',
         "Il existe déjà un horaire pour ce lieu."),
    ]

    @api.constrains('daily_capacity')
    def _check_daily_capacity(self):
        for rec in self:
            if rec.daily_capacity < 1:
                raise ValidationError(_("La capacité quotidienne doit être au moins 1."))

    def _day_is_open(self, weekday_index):
        """weekday_index: 0=Mon..6=Sun. Returns bool."""
        mapping = [
            self.monday_open, self.tuesday_open, self.wednesday_open,
            self.thursday_open, self.friday_open, self.saturday_open,
            self.sunday_open,
        ]
        return bool(mapping[weekday_index])

    @api.model
    def _seed_default_schedules(self):
        Location = self.env['repair.pickup.location']
        for location in Location.search([]):
            if not self.search([('location_id', '=', location.id)], limit=1):
                self.create({'location_id': location.id})
