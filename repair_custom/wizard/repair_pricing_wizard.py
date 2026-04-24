import logging

from odoo import models, fields, api, _, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class RepairPricingWizard(models.TransientModel):
    _name = 'repair.pricing.wizard'
    _description = "Calculatrice de Prix et Ventilation (Devis)"

    repair_id = fields.Many2one('repair.order', required=True)
    internal_notes = fields.Text(string="Notes technicien", readonly=True)

    # --- CONFIGURATION ---
    use_template = fields.Boolean("Utiliser un modèle", default=False)
    invoice_template_id = fields.Many2one(
        'repair.invoice.template', string="Modèle de Facturation"
    )

    target_total_amount = fields.Monetary(
        "Total HT Souhaité", required=True, currency_field='currency_id'
    )
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id
    )

    extra_parts_ids = fields.One2many(
        'repair.pricing.part', 'wizard_id', string="Pièces Spécifiques"
    )
    parts_mode = fields.Selection([
        ('included', 'Déduire du Total'),
        ('added', 'Ajouter au Total'),
    ], string="Gestion des pièces", default='included', required=True)

    manual_label = fields.Char(
        "Libellé de la ligne", default="Forfait Atelier / Main d'œuvre"
    )
    manual_product_id = fields.Many2one(
        'product.product',
        string="Article Service",
        domain=[('type', '=', 'service')],
        help="Article utilisé pour la ligne de facturation libre",
    )

    # --- DÉTAILS / NOTES ---
    device_name = fields.Char(string="Appareil", readonly=True)
    technician_employee_id = fields.Many2one(
        'hr.employee', string="Technicien", readonly=True
    )
    work_time = fields.Float(related="repair_id.work_time", readonly=True)
    add_work_details = fields.Boolean(
        "Ajouter le détail des travaux", default=True
    )
    work_details = fields.Text("Détail à afficher sur la facture")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        service = self.env['product.product'].search(
            [('type', '=', 'service'), ('default_code', '=', 'SERV')], limit=1
        )
        if not service:
            service = self.env['product.product'].search(
                [('type', '=', 'service')], limit=1
            )
        if service:
            res['manual_product_id'] = service.id

        active_repair_id = (self.env.context.get('default_repair_id')
                            or self.env.context.get('active_id'))
        # Theme A: batch walkthrough removed. active_model='repair.batch' is
        # ignored; caller must pass default_repair_id explicitly.
        if active_repair_id:
            repair = self.env['repair.order'].browse(active_repair_id)
            if repair.exists():
                clean_notes = tools.html2plaintext(
                    repair.internal_notes or ""
                ).strip()
                res['repair_id'] = repair.id
                res['work_details'] = clean_notes
                res['internal_notes'] = clean_notes
                res['device_name'] = repair.device_id_name
                res['technician_employee_id'] = (
                    repair.technician_employee_id.id or False
                )
                res['work_time'] = repair.work_time

        return res

    def action_confirm(self):
        self.ensure_one()
        lines = self._get_invoice_lines_formatted()
        try:
            with self.env.cr.savepoint():
                return self._create_quote(lines)
        except Exception as e:
            _logger.error("Failed to create quote: %s", e)
            raise UserError(_("Erreur lors de la création du devis : %s") % e)

    def _get_invoice_lines_formatted(self):
        """Generate the list of line dicts for the sale.order: one header
        section + N product lines + optional notes section."""
        lines_data = self._prepare_lines_data()
        invoice_lines_vals = []

        invoice_lines_vals.append({
            'display_type': 'line_section',
            'name': self._get_header_label(),
            'product_id': False,
        })

        for line in lines_data:
            invoice_lines_vals.append({
                'display_type': 'product',
                'product_id': line['product_id'],
                'name': line['name'],
                'quantity': line['quantity'],
                'price_unit': line['price_unit'],
                'tax_ids': line['tax_ids'],
            })

        if self.add_work_details and self.work_details:
            invoice_lines_vals.append({
                'display_type': 'line_section',
                'name': "Détails",
                'product_id': False,
            })
            invoice_lines_vals.append({
                'display_type': 'line_note',
                'name': self.work_details,
                'product_id': False,
            })

        return invoice_lines_vals

    def _prepare_lines_data(self):
        """HT amount distribution between parts and labour."""
        total_parts_ht = sum(p.price_subtotal for p in self.extra_parts_ids)

        if self.parts_mode == 'included':
            work_amount_ht = self.target_total_amount - total_parts_ht
            if work_amount_ht < 0:
                raise UserError(_(
                    "Le montant des pièces (%s HT) dépasse le total souhaité "
                    "(%s HT) !"
                ) % (total_parts_ht, self.target_total_amount))
        else:
            work_amount_ht = self.target_total_amount

        lines_list = []

        for part in self.extra_parts_ids:
            lines_list.append({
                'product_id': part.product_id.id,
                'name': part.name or part.product_id.name,
                'quantity': part.quantity,
                'price_unit': part.price_unit,
                'tax_ids': part.product_id.taxes_id.ids,
            })

        if self.use_template:
            if not self.invoice_template_id:
                raise UserError(_(
                    "Veuillez sélectionner un modèle de facturation."
                ))
            total_weight = sum(
                l.weight_percentage for l in self.invoice_template_id.line_ids
            )
            if total_weight == 0:
                raise UserError(_(
                    "Le modèle doit avoir des pourcentages > 0."
                ))
            for t_line in self.invoice_template_id.line_ids:
                share = t_line.weight_percentage / total_weight
                lines_list.append({
                    'product_id': t_line.product_id.id,
                    'name': t_line.name,
                    'quantity': 1,
                    'price_unit': work_amount_ht * share,
                    'tax_ids': t_line.product_id.taxes_id.ids,
                })
        else:
            if not self.manual_product_id:
                raise UserError(_(
                    "Veuillez sélectionner un Article Service."
                ))
            lines_list.append({
                'product_id': self.manual_product_id.id,
                'name': self.manual_label,
                'quantity': 1,
                'price_unit': work_amount_ht,
                'tax_ids': self.manual_product_id.taxes_id.ids,
            })

        return lines_list

    def _get_header_label(self):
        device_name = self.device_name or "Appareil Inconnu"
        sn = self.repair_id.lot_id.name or ''
        label = f"Réparation : {device_name}"
        if sn:
            label += f" (S/N: {sn})"
        return label

    def _create_quote(self, lines_list_dicts):
        """Create exactly one sale.order linked to self.repair_id."""
        if self.repair_id.sale_order_id:
            raise UserError(_("Un devis est déjà lié à cette réparation."))

        formatted_lines = []
        for l in lines_list_dicts:
            raw_type = l.get('display_type', False)
            dtype = False if raw_type == 'product' else raw_type
            val = {
                'display_type': dtype,
                'name': l['name'],
                'product_id': l['product_id'],
            }
            if dtype == 'product' or not dtype:
                val.update({
                    'product_uom_qty': l['quantity'],
                    'price_unit': l['price_unit'],
                    'tax_id': [(6, 0, l['tax_ids'])],
                })
            formatted_lines.append((0, 0, val))

        template = self.env.ref(
            'repair_custom.sale_order_template_repair_quote'
        )
        sale_order = self.env['sale.order'].create({
            'partner_id': self.repair_id.partner_id.id,
            'order_line': formatted_lines,
            'sale_order_template_id': template.id,
            'repair_order_ids': [(4, self.repair_id.id)],
        })
        self.repair_id.sale_order_id = sale_order.id

        return {
            'name': _("Devis Généré"),
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': sale_order.id,
            'view_mode': 'form',
        }


class RepairPricingPart(models.TransientModel):
    _name = 'repair.pricing.part'
    _description = "Ligne de pièce manuelle"

    wizard_id = fields.Many2one('repair.pricing.wizard', string="Wizard Lien")
    product_id = fields.Many2one(
        'product.product', string="Pièce", required=True,
        domain=[('type', '!=', 'service')],
    )
    name = fields.Char("Description")
    quantity = fields.Float(default=1.0)
    price_unit = fields.Float("Prix Unit. HT", required=True)
    price_subtotal = fields.Float(compute='_compute_sub', string="Total HT")

    @api.depends('quantity', 'price_unit')
    def _compute_sub(self):
        for rec in self:
            rec.price_subtotal = rec.quantity * rec.price_unit

    @api.onchange('product_id')
    def _onchange_product(self):
        if self.product_id:
            self.price_unit = self.product_id.lst_price
            self.name = self.product_id.name
