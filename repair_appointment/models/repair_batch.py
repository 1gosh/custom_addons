from odoo import api, fields, models, _


class RepairBatch(models.Model):
    _inherit = 'repair.batch'

    appointment_ids = fields.One2many(
        'repair.pickup.appointment', 'batch_id',
        string='Rendez-vous de retrait',
    )
    current_appointment_id = fields.Many2one(
        'repair.pickup.appointment',
        string='Rendez-vous en cours',
        compute='_compute_current_appointment',
        store=False,
    )

    @api.depends('appointment_ids.state')
    def _compute_current_appointment(self):
        for batch in self:
            non_terminal = batch.appointment_ids.filtered(
                lambda a: a.state in ('pending', 'scheduled')
            )
            batch.current_appointment_id = non_terminal[:1]

    def action_create_pickup_appointment(self, notify=True):
        """Create a pending appointment for this batch. Idempotent:
        returns the existing non-terminal appointment if one exists.
        If `notify=True` and a mail template for the initial notification
        is configured, send it and stamp `notification_sent_at`.

        The initial notification template itself is owned by sub-project 3.
        This method only provides the hook.
        """
        self.ensure_one()
        if self.current_appointment_id:
            return self.current_appointment_id
        apt = self.env['repair.pickup.appointment'].create({
            'batch_id': self.id,
        })
        if notify:
            template = self.env.ref(
                'repair_appointment.mail_template_pickup_ready',
                raise_if_not_found=False,
            )
            if template:
                template.send_mail(apt.id, force_send=False)
                apt.notification_sent_at = fields.Datetime.now()
        return apt
