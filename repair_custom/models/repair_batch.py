from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError

class RepairBatch(models.Model):
    _name = 'repair.batch'
    _description = "Dossier de Dépôt"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc'
    name = fields.Char("Réf. Dossier", required=True, copy=False, readonly=True, default='New')
    active = fields.Boolean(default=True)
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

    delivery_state = fields.Selection(
        [
            ('none', "Aucune livraison"),
            ('partial', "Partiellement livré"),
            ('delivered', "Livré"),
            ('abandoned', "Abandonné"),
        ],
        string="État livraison",
        compute='_compute_delivery_state',
        store=True,
        default='none',
    )

    @api.depends('repair_ids.delivery_state')
    def _compute_delivery_state(self):
        for batch in self:
            repairs = batch.repair_ids
            if not repairs:
                batch.delivery_state = 'none'
                continue
            eligible = repairs.filtered(lambda r: r.delivery_state != 'abandoned')
            if not eligible:
                batch.delivery_state = 'abandoned'
                continue
            delivered = eligible.filtered(lambda r: r.delivery_state == 'delivered')
            if len(delivered) == len(eligible):
                batch.delivery_state = 'delivered'
            elif delivered:
                batch.delivery_state = 'partial'
            else:
                batch.delivery_state = 'none'

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

    def action_pickup_start(self):
        """Counter entry point. Route to linked sale.order or open the
        pricing wizard in invoice mode. Invoice creation happens wherever;
        delivery transition is driven by the account.move post hook.
        """
        self.ensure_one()
        eligible = self.repair_ids.filtered(
            lambda r: r.delivery_state == 'none'
            and r.state in ('done', 'irreparable')
        )
        if not eligible:
            raise UserError(_(
                "Aucune réparation en attente de livraison dans ce dossier."
            ))

        sale_orders = self.repair_ids.mapped('sale_order_id')
        if sale_orders:
            return {
                'name': _("Devis / Bon de Commande"),
                'type': 'ir.actions.act_window',
                'res_model': 'sale.order',
                'res_id': sale_orders[:1].id,
                'view_mode': 'form',
                'target': 'current',
            }

        return {
            'name': _("Création du Devis"),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.pricing.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_repair_id': eligible[:1].id,
            },
        }

    def action_mark_delivered(self):
        """Per-batch UI, per-repair data.

        Transitions all eligible repairs to delivered:
        - repairs in state {done, irreparable} with delivery_state='none'
        - repairs with quote_state='refused' and delivery_state='none'
          (client takes un-repaired device back; state silently set to cancel)

        Runs side effects via `action_repair_delivered`, marks the linked
        appointment done, and posts a chatter note.
        """
        self.ensure_one()
        eligible = self.repair_ids.filtered(
            lambda r: r.delivery_state == 'none'
            and (r.state in ('done', 'irreparable')
                 or r.quote_state == 'refused')
        )
        if not eligible:
            raise UserError(_(
                "Aucune réparation à livrer dans ce dossier."
            ))

        # Partial-acceptance branch: refused-quote repairs go out un-repaired.
        # Silent state='cancel' side effect + delivery_state='delivered';
        # no SAR, no invoice (no approved SO to invoice from).
        refused_pickup = eligible.filtered(
            lambda r: r.quote_state == 'refused'
            and r.state not in ('cancel', 'irreparable')
        )
        for rec in refused_pickup:
            rec.state = 'cancel'
        refused_pickup.write({'delivery_state': 'delivered'})

        normal_pickup = eligible - refused_pickup
        if normal_pickup:
            normal_pickup.action_repair_delivered()

        current_apt = getattr(self, 'current_appointment_id', None)
        if current_apt and current_apt.state == 'scheduled':
            current_apt.action_mark_done()

        self.message_post(body=_(
            "Dossier livré : %d appareil(s) remis au client."
        ) % len(eligible))
        return True

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

    has_invoiceable_quotes = fields.Boolean(
        compute='_compute_has_invoiceable_quotes',
        string="Devis à facturer",
    )

    @api.depends('repair_ids.is_quote_invoiceable')
    def _compute_has_invoiceable_quotes(self):
        for batch in self:
            batch.has_invoiceable_quotes = any(
                r.is_quote_invoiceable for r in batch.repair_ids
            )

    def action_invoice_approved_quotes(self):
        """Batch-form button: consolidate all eligible approved quotes into
        one account.move."""
        self.ensure_one()
        eligible = self.repair_ids.filtered('is_quote_invoiceable')
        if not eligible:
            raise UserError(_(
                "Aucun devis accepté à facturer dans ce dossier."
            ))
        return self._invoice_approved_quotes(eligible)

    def _invoice_approved_quotes(self, repairs):
        """Core helper: consolidate sale.orders of `repairs` into one
        account.move with per-repair section headers. Shared by the repair-
        form button, the batch-form button, and the sale.order replacement
        button."""
        self.ensure_one()
        if not repairs:
            raise UserError(_("Aucune réparation sélectionnée."))
        sale_orders = repairs.mapped('sale_order_id')
        if not sale_orders:
            raise UserError(_("Aucun devis lié aux réparations sélectionnées."))

        moves = sale_orders._create_invoices()
        for move in moves:
            self._inject_repair_section_headers(move)
            if not move.batch_id:
                move.batch_id = self.id
            # repair_id auto-stamped via account.move.create override when unique

        if len(moves) == 1:
            return {
                'name': _("Facture Générée"),
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'res_id': moves.id,
                'view_mode': 'form',
            }
        return {
            'name': _("Factures Générées"),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', moves.ids)],
        }

    def _inject_repair_section_headers(self, move):
        """Insert a display_type='line_section' header before each source SO's
        lines on a consolidated invoice. Labels mirror today's wizard format.

        Legacy SOs (linked to N repairs) fall back to the SO name — forward
        decision: no post-migration split, handle gracefully at read time."""
        self.ensure_one()
        lines_by_so = {}
        for line in move.invoice_line_ids.sorted('sequence'):
            if line.display_type in ('line_section', 'line_note'):
                continue
            sos = line.sale_line_ids.mapped('order_id')
            if not sos:
                continue
            so = sos[:1]
            lines_by_so.setdefault(so.id, []).append(line)

        seq = 0
        AccountMoveLine = self.env['account.move.line']
        for so_id, lines in lines_by_so.items():
            so = self.env['sale.order'].browse(so_id)
            if len(so.repair_order_ids) == 1:
                repair = so.repair_order_ids
                label = _("Réparation : %s") % (repair.device_id_name or so.name)
                if repair.serial_number:
                    label += _(" (S/N: %s)") % repair.serial_number
            else:
                label = _("Devis : %s") % so.name

            seq += 1
            AccountMoveLine.create({
                'move_id': move.id,
                'display_type': 'line_section',
                'name': label,
                'sequence': seq,
            })
            for line in lines:
                seq += 1
                line.sequence = seq

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
