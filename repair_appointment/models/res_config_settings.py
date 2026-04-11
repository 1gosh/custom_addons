from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    appointment_booking_horizon_days = fields.Integer(
        string="Horizon de réservation (jours)",
        config_parameter='repair_appointment.booking_horizon_days',
        default=14,
    )
    appointment_min_lead_days = fields.Integer(
        string="Délai minimum avant RDV (jours)",
        config_parameter='repair_appointment.min_lead_days',
        default=2,
    )
    appointment_reminder_delay_days = fields.Integer(
        string="Délai avant rappel (jours)",
        config_parameter='repair_appointment.reminder_delay_days',
        default=3,
    )
    appointment_escalation_delay_days = fields.Integer(
        string="Délai avant escalade (jours)",
        config_parameter='repair_appointment.escalation_delay_days',
        default=3,
    )
