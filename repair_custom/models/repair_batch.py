from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError

class RepairBatch(models.Model):
    _name = 'repair.batch'
    _description = "Dossier de Dépôt"
    _order = 'date desc'
    name = fields.Char("Réf. Dossier", required=True, copy=False, readonly=True, default='New')
    date = fields.Datetime(string="Date de création", default=lambda self: fields.Datetime.now())
    repair_ids = fields.One2many('repair.order', 'batch_id', string="Réparations")
    partner_id = fields.Many2one('res.partner', string="Client")
    company_id = fields.Many2one('res.company', string="Société", default=lambda self: self.env.company)
    repair_count = fields.Integer(string="Nb Appareils", compute='_compute_repair_count', store=True)
    state = fields.Selection([('draft', 'Brouillon'), 
                            ('confirmed', 'En attente'), 
                            ('under_repair', 'En cours'), 
                            ('processed', 'Traité')], 
                            string="État", 
                            compute='_compute_state', 
                            store=True, default='draft'
    )

    @api.depends('repair_ids')
    def _compute_repair_count(self):
        for rec in self: rec.repair_count = len(rec.repair_ids)

    @api.depends('repair_ids.state')
    def _compute_state(self):
        for batch in self:
            if not batch.repair_ids:
                batch.state = 'draft'
                continue
            states = set(batch.repair_ids.mapped('state'))
            # Processed: all repairs are done, cancelled, or irreparable
            if states.issubset({'done', 'cancel', 'irreparable'}): batch.state = 'processed'
            # Under repair: any repair is under_repair
            elif 'under_repair' in states: batch.state = 'under_repair'
            # Confirmed: all non-cancelled repairs are confirmed
            elif all(r.state == 'confirmed' for r in batch.repair_ids if r.state != 'cancel'): batch.state = 'confirmed'
            # Draft: fallback for mixed states or all draft
            else: batch.state = 'draft'

    ready_for_pickup_notification = fields.Boolean(
        string="Prêt à notifier",
        compute='_compute_ready_for_pickup_notification',
        store=True,
    )

    @api.depends(
        'repair_ids.state',
        'repair_ids.delivery_state',
    )
    def _compute_ready_for_pickup_notification(self):
        for batch in self:
            non_abandoned = batch.repair_ids.filtered(
                lambda r: r.delivery_state != 'abandoned'
            )
            if not non_abandoned:
                batch.ready_for_pickup_notification = False
                continue
            all_terminal = all(
                r.state in ('done', 'irreparable') for r in non_abandoned
            )
            if not all_terminal:
                batch.ready_for_pickup_notification = False
                continue
            current_apt = getattr(batch, 'current_appointment_id', False)
            if current_apt and current_apt.notification_sent_at:
                batch.ready_for_pickup_notification = False
                continue
            batch.ready_for_pickup_notification = True

    def action_notify_client_ready(self):
        """Trigger initial pickup-ready notification for this batch.

        Delegates to repair_appointment's `action_create_pickup_appointment(notify=True)`.
        Idempotent: if the batch already has a non-terminal appointment with
        `notification_sent_at` stamped, return True without creating a new one.
        Raises UserError if the batch is not yet ready for notification.
        """
        self.ensure_one()
        current_apt = self.current_appointment_id
        if current_apt and current_apt.notification_sent_at:
            # Already notified — idempotent no-op.
            return True
        if not self.ready_for_pickup_notification:
            raise UserError(_(
                "Ce dossier n'est pas prêt pour une notification de retrait."
            ))
        return self.action_create_pickup_appointment(notify=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                seq_name = self.env['ir.sequence'].next_by_code('repair.batch') or 'New'
                prefix = ""
                if vals.get('partner_id'):
                    partner = self.env['res.partner'].browse(vals['partner_id'])
                    if partner.name: prefix = f"{partner.name.upper().replace(' ', '').replace('.', '')[:4]}-"
                vals['name'] = f"{prefix}{seq_name}"
        return super(RepairBatch, self).create(vals_list)
