from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RepairIntakeFeeWizard(models.TransientModel):
    _name = 'repair.intake.fee.wizard'
    _description = "Encaissement de la prise en charge / diagnostic"

    batch_id = fields.Many2one('repair.batch', required=True)
    partner_id = fields.Many2one(related='batch_id.partner_id', readonly=True)
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id
    )
    line_ids = fields.One2many(
        'repair.intake.fee.wizard.line', 'wizard_id', string="Appareils"
    )
    amount_total_ht = fields.Monetary(
        string="Total HT à encaisser", compute='_compute_amounts',
        currency_field='currency_id',
    )
    amount_total_ttc = fields.Monetary(
        string="Total TTC à encaisser", compute='_compute_amounts',
        currency_field='currency_id',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        batch_id = self.env.context.get('default_batch_id')
        if not batch_id:
            return res
        batch = self.env['repair.batch'].browse(batch_id)
        default_amount = float(
            self.env['ir.config_parameter'].sudo().get_param(
                'repair_custom.intake_fee_amount_ht', 0.0
            ) or 0.0
        )
        lines = []
        for repair in batch.repair_ids.filtered(lambda r: r.intake_fee_state == 'to_collect'):
            auto_exempt = repair.repair_warranty in ('sar', 'sav')
            lines.append((0, 0, {
                'repair_id': repair.id,
                'to_invoice': not auto_exempt,
                'amount_ht': default_amount,
                'exempt_reason': _("Garantie SAR/SAV") if auto_exempt else False,
            }))
        res['line_ids'] = lines
        return res

    @api.depends('line_ids.to_invoice', 'line_ids.amount_ht')
    def _compute_amounts(self):
        service_tax = self.env['sale.order.line']._get_service_tax()
        tax_rate = 1.0
        if service_tax and service_tax.amount_type == 'percent':
            tax_rate += service_tax.amount / 100.0
        for wiz in self:
            total_ht = sum(l.amount_ht for l in wiz.line_ids if l.to_invoice)
            wiz.amount_total_ht = total_ht
            wiz.amount_total_ttc = total_ht * tax_rate

    def action_confirm(self):
        self.ensure_one()
        to_invoice_lines = self.line_ids.filtered('to_invoice')
        exempt_lines = self.line_ids - to_invoice_lines

        for line in exempt_lines:
            line.repair_id.write({
                'intake_fee_exempt': True,
                'intake_fee_exempt_reason': line.exempt_reason or _("Exonéré au comptoir"),
            })

        if not to_invoice_lines:
            self.batch_id.message_post(body=_(
                "Prise en charge : aucun appareil facturé (tous exonérés)."
            ))
            return {'type': 'ir.actions.act_window_close'}

        fee_product = self.env['repair.pricing.wizard']._get_intake_fee_product()
        if not fee_product:
            raise UserError(_(
                "Aucun article de prise en charge configuré (Réglages > Réparations)."
            ))
        service_tax = self.env['sale.order.line']._get_service_tax()
        tax_ids = service_tax.ids if service_tax else fee_product.taxes_id.ids

        invoice_lines = []
        for line in to_invoice_lines:
            repair = line.repair_id
            label = _("Prise en charge / Diagnostic — %s") % (
                repair.device_id_name or repair.name
            )
            if repair.lot_id:
                label += _(" (S/N: %s)") % repair.lot_id.name
            invoice_lines.append((0, 0, {
                'product_id': fee_product.id,
                'name': label,
                'quantity': 1,
                'price_unit': line.amount_ht,
                'tax_ids': [(6, 0, tax_ids)],
            }))

        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.batch_id.partner_id.id,
            'invoice_origin': self.batch_id.name,
            'batch_id': self.batch_id.id,
            'invoice_line_ids': invoice_lines,
        })
        move.with_context(skip_repair_pickup_transition=True).action_post()

        for line in to_invoice_lines:
            line.repair_id.write({
                'intake_fee_invoice_id': move.id,
                'intake_fee_amount_ht': line.amount_ht,
            })
            line.repair_id.message_post(body=_(
                "Prise en charge / Diagnostic facturée (%s)."
            ) % move.name)

        self.batch_id.message_post(body=_(
            "Prise en charge encaissée : %d appareil(s), facture %s."
        ) % (len(to_invoice_lines), move.name))

        return move.action_register_payment()


class RepairIntakeFeeWizardLine(models.TransientModel):
    _name = 'repair.intake.fee.wizard.line'
    _description = "Ligne d'encaissement de prise en charge"

    wizard_id = fields.Many2one(
        'repair.intake.fee.wizard', required=True, ondelete='cascade'
    )
    repair_id = fields.Many2one('repair.order', required=True, readonly=True)
    device_name = fields.Char(related='repair_id.device_id_name', readonly=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    to_invoice = fields.Boolean(string="À encaisser", default=True)
    amount_ht = fields.Monetary(string="Montant HT", currency_field='currency_id')
    exempt_reason = fields.Char(string="Motif d'exonération")

    @api.onchange('to_invoice')
    def _onchange_to_invoice(self):
        for line in self:
            if line.to_invoice:
                line.exempt_reason = False
            elif not line.exempt_reason:
                line.exempt_reason = _("Exonéré au comptoir")
