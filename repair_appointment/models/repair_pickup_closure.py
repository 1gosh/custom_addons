from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class RepairPickupClosure(models.Model):
    _name = 'repair.pickup.closure'
    _description = 'Jour de fermeture pour retraits'
    _order = 'date_from desc'

    name = fields.Char(required=True)
    location_id = fields.Many2one(
        'repair.pickup.location',
        string='Lieu (vide = tous)',
        ondelete='cascade',
        help="Si laissé vide, la fermeture s'applique à tous les lieux.",
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    active = fields.Boolean(default=True)

    @api.constrains('date_from', 'date_to')
    def _check_date_range(self):
        for rec in self:
            if rec.date_to < rec.date_from:
                raise ValidationError(_("La date de fin doit être postérieure ou égale à la date de début."))

    def _covers(self, day, location):
        """Return True if this closure covers `day` at `location`."""
        self.ensure_one()
        if not (self.date_from <= day <= self.date_to):
            return False
        if self.location_id and self.location_id != location:
            return False
        return True
