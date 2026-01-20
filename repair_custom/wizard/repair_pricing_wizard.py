from odoo import models, fields, api, _, tools
from odoo.exceptions import UserError
import json

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

    use_template = fields.Boolean("Utiliser un modèle", default=False)
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

    batch_id = fields.Many2one('repair.batch', string="Dossier Batch")
    
    remaining_repair_ids = fields.Many2many('repair.order', string="Réparations restantes")
    accumulated_lines_json = fields.Text(default="[]") 
    step_info = fields.Char(readonly=True)

    # --- DÉTAILS / NOTES ---
    device_name = fields.Char(string="Appareil", readonly=True)
    technician_employee_id = fields.Many2one('hr.employee', string="Technicien", readonly=True)
    work_time = fields.Float(related="repair_id.work_time", readonly=True)
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
        context = self.env.context
        
        # CAS 1 : On vient d'un BATCH
        if context.get('active_model') == 'repair.batch' and context.get('active_id'):
            batch = self.env['repair.batch'].browse(context.get('active_id'))
            if batch.repair_ids:
                # On prend tous les repairs du batch
                all_repairs = batch.repair_ids
                first_repair = all_repairs[0]
                remaining = all_repairs[1:] # Les autres
                
                res['batch_id'] = batch.id
                res['repair_id'] = first_repair.id
                res['remaining_repair_ids'] = [(6, 0, remaining.ids)]
                res['step_info'] = f"Appareil 1 / {len(all_repairs)}"
                
                # On charge les infos du PREMIER appareil (notes, device name...)
                # (Copie de votre logique existante pour peupler les champs)
                clean_notes = tools.html2plaintext(first_repair.internal_notes or "")
                res['work_details'] = clean_notes.strip()
                res['internal_notes'] = clean_notes.strip()
                res['device_name'] = first_repair.device_id_name
                res['technician_employee_id'] = first_repair.technician_employee_id.id
                
        if active_repair_id:
            repair = self.env['repair.order'].browse(active_repair_id)
            if repair.exists():
                if context.get('default_generation_type') == 'quote':
                    # Si mode Devis -> On prend les notes d'estimation (quotation_notes)
                    raw_notes = repair.quotation_notes or ""
                else:
                    # Si mode Facture (ou par défaut) -> On prend les notes techniques (internal_notes)
                    raw_notes = repair.internal_notes or ""

                clean_notes = tools.html2plaintext(raw_notes).strip()
                res['work_details'] = clean_notes.strip()
                res['internal_notes'] = clean_notes.strip()
                res['device_name'] = repair.device_id_name
                res['technician_employee_id'] = repair.technician_employee_id.id or False

        return res

    def action_next_step(self):
        """ Valide l'étape actuelle, stocke les données, et charge l'appareil suivant """
        self.ensure_one()
        
        # 1. Générer les lignes pour l'appareil actuel
        current_invoice_lines = self._get_invoice_lines_formatted()
        
        # 2. Récupérer l'historique et ajouter les nouvelles lignes
        history = json.loads(self.accumulated_lines_json)
        history.extend(current_invoice_lines)
        
        # 3. Préparer le PROCHAIN appareil
        next_repair = self.remaining_repair_ids[0]
        new_remaining = self.remaining_repair_ids[1:]
        
        # Calcul du numéro d'étape pour l'affichage
        total_repairs = len(self.batch_id.repair_ids)
        next_step_number = total_repairs - len(new_remaining)
        new_step_info = f"Appareil {next_step_number} / {total_repairs}"

        # 4. RESET DES VALEURS (Pour avoir un formulaire propre pour le suivant)
        # Il faut re-exécuter la logique de chargement des notes/produits par défaut
        clean_notes = tools.html2plaintext(next_repair.internal_notes or "")
        
        # On met à jour le wizard pour la prochaine vue
        self.write({
            'repair_id': next_repair.id,
            'remaining_repair_ids': [(6, 0, new_remaining.ids)],
            'accumulated_lines_json': json.dumps(history),
            'step_info': new_step_info,
            
            # Reset des champs de saisie
            'target_total_amount': 0.0,
            'extra_parts_ids': [(5, 0, 0)], # Vider les pièces
            'manual_product_id': self.env['product.product'].search([('type', '=', 'service'), ('default_code', '=', 'SERV')], limit=1).id,
            
            # Chargement des infos du nouvel appareil
            'device_name': next_repair.device_id_name,
            'technician_employee_id': next_repair.technician_employee_id.id,
            'internal_notes': clean_notes.strip(),
            'work_details': clean_notes.strip(),
        })
        
        # 5. RECHARGER LA VUE (Action window qui se rappelle elle-même)
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _get_invoice_lines_formatted(self):
        """ Helper qui génère la structure exacte des lignes pour 'account.move' 
            (En-tête + Lignes + Notes) pour l'appareil EN COURS.
        """
        lines_data = self._prepare_lines_data() # Votre méthode existante qui calcule les prix
        invoice_lines_vals = []

        # A. HEADER (Nom appareil + SN)
        invoice_lines_vals.append({
            'display_type': 'line_section',
            'name': self._get_header_label(),
            'product_id': False,
        })

        # B. LIGNES (Prix)
        for line in lines_data:
            invoice_lines_vals.append({
                'display_type': 'product',
                'product_id': line['product_id'],
                'name': line['name'],
                'quantity': line['quantity'],
                'price_unit': line['price_unit'],
                'tax_ids': line['tax_ids'], # Attention: json ne gère pas les objets recordset, on stockera des IDs
            })

        # C. DETAILS (Notes)
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

    def action_confirm(self):
        """ Modifiée pour gérer le Batch final """
        self.ensure_one()
        
        current_lines = self._get_invoice_lines_formatted()
        final_lines_list = json.loads(self.accumulated_lines_json)
        final_lines_list.extend(current_lines)

        if self.generation_type == 'invoice':
            return self._create_global_invoice(final_lines_list)
        else:
            return self._create_global_sale_order(final_lines_list)

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

    def _create_global_invoice(self, lines_list_dicts, is_quote=False):
        """ Crée une facture à partir d'une liste de dictionnaires """
        formatted_lines = []
        
        for l in lines_list_dicts:
            dtype = l.get('display_type', 'product')
            val = {
                'display_type': dtype,
                'name': l['name'],
                'product_id': l['product_id'],
            }
            # Champs spécifiques aux lignes produits (pas notes/sections)
            if dtype == 'product' or not dtype:
                val.update({
                    'quantity': l['quantity'],
                    'price_unit': l['price_unit'],
                    'tax_ids': [(6, 0, l['tax_ids'])],
                })
            
            formatted_lines.append((0, 0, val))

        # Partenaire : soit celui du Batch, soit celui du dernier repair (c'est le même)
        partner = self.batch_id.partner_id if self.batch_id else self.repair_id.partner_id
        
        move_vals = {
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_line_ids': formatted_lines,
        }
        
        # Gestion Batch : on lie la facture au batch si possible (via un champ custom sur account.move)
        # if self.batch_id: move_vals['batch_id'] = self.batch_id.id
        
        move = self.env['account.move'].create(move_vals)
        
        return {
            'name': _("Facture Batch"),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
        } 

    def _create_global_sale_order(self, lines_list_dicts):
        """ Crée un devis (sale.order) depuis la liste de dictionnaires """
        
        # Vérification de sécurité (uniquement si pas en batch, car en batch on peut avoir plusieurs repairs)
        if not self.batch_id and self.repair_id.sale_order_id:
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
            # différences de nommage champs entre Invoice et Sale Order
            if dtype == 'product' or not dtype:
                val.update({
                    'product_uom_qty': l['quantity'],
                    'price_unit': l['price_unit'],
                    'tax_id': [(6, 0, l['tax_ids'])],
                })
            
            formatted_lines.append((0, 0, val))

        partner = self.batch_id.partner_id if self.batch_id else self.repair_id.partner_id
        
        so_vals = {
            'partner_id': partner.id,
            'order_line': formatted_lines,
        }
        
        # Lier le Devis aux réparations
        if self.batch_id:
             # Si batch, on lie à toutes les réparations du batch
             so_vals['repair_order_ids'] = [(6, 0, self.batch_id.repair_ids.ids)]
        else:
             # Sinon juste à l'unique
             so_vals['repair_order_ids'] = [(4, self.repair_id.id)]

        sale_order = self.env['sale.order'].create(so_vals)
        
        # Mise à jour inverse (Lier la réparation au devis)
        if self.batch_id:
            self.batch_id.repair_ids.write({'sale_order_id': sale_order.id})
        else:
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