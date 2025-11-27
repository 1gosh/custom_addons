from odoo import models, fields, api, _, tools
from odoo.exceptions import UserError

class RepairPricingWizard(models.TransientModel):
    _name = 'repair.pricing.wizard'
    _description = "Calculatrice de Prix et Ventilation"

    repair_id = fields.Many2one('repair.order', required=True)
    internal_notes = fields.Text(string="Notes technicien", readonly=True)
    
    # --- CONFIGURATION ---
    generation_type = fields.Selection([
        ('invoice', 'Facture (Directe)'),
        ('quote', 'Devis (Bon de Commande)'),
    ], string="Type de document", default='invoice', required=True)

    use_template = fields.Boolean("Utiliser un modèle", default=True)
    invoice_template_id = fields.Many2one('repair.invoice.template', string="Modèle de Facturation")

    target_total_amount = fields.Monetary("Total HT Souhaité", required=True, currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    extra_parts_ids = fields.One2many('repair.pricing.part', 'wizard_id', string="Pièces Spécifiques")
    parts_mode = fields.Selection([
        ('included', 'Déduire du Total'),
        ('added', 'Ajouter au Total'),
    ], string="Gestion des pièces", default='included', required=True)

    manual_label = fields.Char("Libellé de la ligne", default="Forfait Atelier / Main d'œuvre")
    
    manual_product_id = fields.Many2one(
        'product.product', 
        string="Article Service", 
        domain=[('type', '=', 'service')],
        help="Article utilisé pour la ligne de facturation libre"
    )

    # --- DÉTAILS / NOTES ---
    device_name = fields.Char(string="Appareil", readonly=True)
    technician_employee_id = fields.Many2one('hr.employee', string="Technicien", readonly=True)
    add_work_details = fields.Boolean("Ajouter le détail des travaux", default=True)
    work_details = fields.Text("Détail à afficher sur la facture")

    @api.model
    def default_get(self, fields):
        res = super(RepairPricingWizard, self).default_get(fields)
        
        # 1. Recherche du service par défaut
        service = self.env['product.product'].search([('type', '=', 'service'), ('default_code', '=', 'SERV')], limit=1)
        if not service:
            service = self.env['product.product'].search([('type', '=', 'service')], limit=1)
        if service:
            res['manual_product_id'] = service.id

        # 2. RÉCUPÉRATION FORCÉE DES NOTES
        # On ne se fie pas au champ related, on va chercher l'objet directement via l'ID du contexte
        active_repair_id = self.env.context.get('default_repair_id') or self.env.context.get('active_id')
        
        if active_repair_id:
            repair = self.env['repair.order'].browse(active_repair_id)
            if repair.exists():
                # On remplit le champ éditable 'work_details' avec les notes internes
                clean_notes = tools.html2plaintext(repair.internal_notes or "") 
                res['work_details'] = clean_notes.strip()
                res['internal_notes'] = clean_notes.strip()
                res['device_name'] = repair.device_id_name
                res['technician_employee_id'] = repair.technician_employee_id.id or False
        return res

    def action_confirm(self):
        self.ensure_one()
        lines_data = self._prepare_lines_data()
        
        if self.generation_type == 'invoice':
            return self._create_invoice(lines_data)
        else:
            return self._create_sale_order(lines_data)

    def _prepare_lines_data(self):
        """ Logique de calcul en HORS TAXE """
        total_parts_ht = sum(p.price_subtotal for p in self.extra_parts_ids)
        
        if self.parts_mode == 'included':
            work_amount_ht = self.target_total_amount - total_parts_ht
            if work_amount_ht < 0:
                raise UserError(_("Le montant des pièces (%s HT) dépasse le total souhaité (%s HT) !") % (total_parts_ht, self.target_total_amount))
        else:
            work_amount_ht = self.target_total_amount

        lines_list = []

        # Pièces
        for part in self.extra_parts_ids:
            lines_list.append({
                'product_id': part.product_id.id,
                'name': part.name or part.product_id.name,
                'quantity': part.quantity,
                'price_unit': part.price_unit,
                'tax_ids': part.product_id.taxes_id.ids,
            })

        # Main d'œuvre
        if self.use_template:
            if not self.invoice_template_id:
                raise UserError(_("Veuillez sélectionner un modèle de facturation."))
            total_weight = sum(l.weight_percentage for l in self.invoice_template_id.line_ids)
            if total_weight == 0:
                raise UserError(_("Le modèle doit avoir des pourcentages > 0."))
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
                raise UserError(_("Veuillez sélectionner un Article Service."))
            lines_list.append({
                'product_id': self.manual_product_id.id,
                'name': self.manual_label,
                'quantity': 1,
                'price_unit': work_amount_ht, 
                'tax_ids': self.manual_product_id.taxes_id.ids,
            })
            
        return lines_list

    def _get_header_label(self):
        """ Génère le titre : Réparation : Marantz XXX (S/N: 123) """

        device_name = self.device_name or "Appareil Inconnu"    
        sn = self.repair_id.serial_number  
        label = f"Réparation : {device_name}"
        if sn:
            label += f" (S/N: {sn})"
        return label

    def _create_invoice(self, lines_data):
        invoice_lines = []

        # --- 1. EN-TÊTE (SECTION) ---
        invoice_lines.append((0, 0, {
            'display_type': 'line_section',
            'name': self._get_header_label(),
            'product_id': False,
        }))

        # --- 2. LIGNES FINANCIÈRES (Prix) ---
        for line in lines_data:
            invoice_lines.append((0, 0, {
                'product_id': line['product_id'],
                'name': line['name'],
                'quantity': line['quantity'],
                'price_unit': line['price_unit'],
                'tax_ids': [(6, 0, line['tax_ids'])],
            }))

        # --- 3. DÉTAILS (TOUT EN BAS) ---
        if self.add_work_details and self.work_details:
            # Section "Détails"
            invoice_lines.append((0, 0, {
                'display_type': 'line_section',
                'name': "Détails de l'intervention",
                'product_id': False,
            }))
            # Note (Texte complet)
            invoice_lines.append((0, 0, {
                'display_type': 'line_note',
                'name': self.work_details,
                'product_id': False,
            }))

        move_vals = {
            'move_type': 'out_invoice',
            'partner_id': self.repair_id.partner_id.id,
            'repair_id': self.repair_id.id,
            'invoice_line_ids': invoice_lines,
        }
        move = self.env['account.move'].create(move_vals)
        return {
            'name': _("Facture Générée"),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
        }

    def _create_sale_order(self, lines_data):
        if self.repair_id.sale_order_id:
             raise UserError(_("Un devis est déjà lié à cette réparation."))

        order_lines = []

        # --- 1. EN-TÊTE (SECTION) ---
        order_lines.append((0, 0, {
            'display_type': 'line_section',
            'name': self._get_header_label(),
            'product_id': False,
        }))

        # --- 2. LIGNES FINANCIÈRES ---
        for line in lines_data:
            order_lines.append((0, 0, {
                'product_id': line['product_id'],
                'name': line['name'],
                'product_uom_qty': line['quantity'],
                'price_unit': line['price_unit'],
                'tax_id': [(6, 0, line['tax_ids'])],
            }))

        # --- 3. DÉTAILS (BAS) ---
        if self.add_work_details and self.work_details:
            order_lines.append((0, 0, {
                'display_type': 'line_section',
                'name': "Détails de l'intervention",
                'product_id': False,
            }))
            order_lines.append((0, 0, {
                'display_type': 'line_note',
                'name': self.work_details,
                'product_id': False,
            }))

        so_vals = {
            'partner_id': self.repair_id.partner_id.id,
            'order_line': order_lines,
            'repair_order_ids': [(4, self.repair_id.id)],
        }
        sale_order = self.env['sale.order'].create(so_vals)
        self.repair_id.sale_order_id = sale_order.id
        return {
            'name': _("Devis Généré"),
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': sale_order.id,
            'view_mode': 'form',
        }

# La classe RepairPricingPart reste inchangée
class RepairPricingPart(models.TransientModel):
    _name = 'repair.pricing.part'
    _description = "Ligne de pièce manuelle"
    
    wizard_id = fields.Many2one('repair.pricing.wizard', string="Wizard Lien")
    product_id = fields.Many2one('product.product', string="Pièce", required=True, domain=[('type', '!=', 'service')])
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