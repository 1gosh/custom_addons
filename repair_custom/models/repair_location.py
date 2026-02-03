from odoo import api, Command, fields, models, _

class RepairPickupLocation(models.Model):
    _name = 'repair.pickup.location'
    _description = 'Repair Pickup Location'
    name = fields.Char(string="Nom du lieu", required=True)
    street = fields.Char(string="Rue")
    street2 = fields.Char(string="Rue (complément)")
    city = fields.Char(string="Ville")
    zip = fields.Char(string="Code postal")
    country_id = fields.Many2one('res.country', string="Pays")
    contact_id = fields.Many2one('res.partner', string="Contact associé")
    company_id = fields.Many2one('res.company', string="Société", default=lambda self: self.env.company)
    def _compute_display_name(self):
        for location in self:
            location.display_name = f"{location.name} – {location.city}" if location.city else location.name