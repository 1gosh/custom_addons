import base64

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

    ready_for_pickup_notification = fields.Boolean(
        compute='_compute_ready_for_pickup_notification',
        store=True,
    )

    @api.depends(
        'repair_ids.state',
        'repair_ids.delivery_state',
        'appointment_ids.state',
        'appointment_ids.notification_sent_at',
    )
    def _compute_ready_for_pickup_notification(self):
        return super()._compute_ready_for_pickup_notification()

    @api.depends('appointment_ids.state')
    def _compute_current_appointment(self):
        for batch in self:
            non_terminal = batch.appointment_ids.filtered(
                lambda a: a.state in ('pending', 'scheduled')
            )
            batch.current_appointment_id = non_terminal[:1]

    def action_open_new_pickup_appointment(self):
        """Open a fresh appointment form pre-wired to this batch.

        Used for the manual creation flow: a staff member organised the
        pickup with the client by phone and wants to create + confirm
        the appointment themselves. The form lets them fill the agreed
        datetimes, save, then press 'Confirmer' to transition to
        scheduled. Distinct from `action_create_pickup_appointment`
        which is the automatic hook fired when a batch is marked done.
        """
        self.ensure_one()
        if self.current_appointment_id:
            return {
                'type': 'ir.actions.act_window',
                'name': _("Rendez-vous de retrait"),
                'res_model': 'repair.pickup.appointment',
                'res_id': self.current_appointment_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _("Nouveau rendez-vous de retrait"),
            'res_model': 'repair.pickup.appointment',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_batch_id': self.id,
            },
        }

    def _build_pickup_quote_attachments(self):
        """Return a list of ir.attachment ids to attach to the pickup-ready
        mail. Attaches the linked sale.order PDF only when the SO is in
        state 'sale' (accepted quote)."""
        self.ensure_one()
        sale_orders = self.repair_ids.mapped('sale_order_id').filtered(
            lambda s: s.state == 'sale'
        )
        if not sale_orders:
            return []
        pdf_content, _mime = self.env['ir.actions.report']._render_qweb_pdf(
            'sale.action_report_saleorder', sale_orders[:1].ids,
        )
        attachment = self.env['ir.attachment'].create({
            'name': _("Devis %s.pdf") % sale_orders[:1].name,
            'type': 'binary',
            'datas': base64.b64encode(pdf_content),
            'res_model': 'sale.order',
            'res_id': sale_orders[:1].id,
            'mimetype': 'application/pdf',
        })
        return [attachment.id]

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
                attachment_ids = self._build_pickup_quote_attachments()
                email_values = None
                if attachment_ids:
                    email_values = {'attachment_ids': [(4, aid) for aid in attachment_ids]}
                template.send_mail(apt.id, force_send=False, email_values=email_values)
                apt.notification_sent_at = fields.Datetime.now()
        return apt
