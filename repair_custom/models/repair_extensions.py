from odoo import api, Command, fields, models, _

class RepairDeviceUnit(models.Model):
    _inherit = 'repair.device.unit'
    repair_order_ids = fields.One2many('repair.order', 'unit_id', string="Réparations associées")
    repair_order_count = fields.Integer(string="Réparations", compute='_compute_repair_order_count')
    def _compute_repair_order_count(self):
        for rec in self:
            rec.repair_order_count = self.env['repair.order'].search_count([('unit_id', '=', rec.id)])
    def action_view_repairs(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Réparations', 'res_model': 'repair.order', 'view_mode': 'tree,form', 'domain': [('unit_id', '=', self.id)], 'context': {'default_unit_id': self.id}}

    functional_state = fields.Selection([
        ('broken', 'En panne'),
        ('fixing', 'En Atelier'),
        ('working', 'Réparé')
    ], string="État physique", compute='_compute_functional_state', store=True)

    @api.depends('repair_order_ids.state')
    def _compute_functional_state(self):
        for unit in self:
            # 1. Y a-t-il une réparation active en ce moment ?
            active_repairs = unit.repair_order_ids.filtered(
                lambda r: r.state in ['confirmed', 'under_repair']
            )
            if active_repairs:
                unit.functional_state = 'fixing'
            else:
                # 2. Sinon, quel est le résultat de la TOUTE DERNIÈRE intervention ?
                # On trie par date de fin (ou d'écriture) décroissante
                last_repair = unit.repair_order_ids.sorted(
                    key=lambda r: r.end_date or r.write_date, 
                    reverse=True
                )
                if last_repair and last_repair[0].state in ['done']:
                    unit.functional_state = 'working'
                else:
                    # Pas d'historique ou dernière réparation annulée/brouillon
                    unit.functional_state = 'broken'

class AccountMove(models.Model):
    _inherit = 'account.move'

    repair_id = fields.Many2one('repair.order', string="Réparation d'origine", readonly=True)
    repair_notes = fields.Text(related='repair_id.internal_notes', string="Notes de l'atelier", readonly=True)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    repair_order_ids = fields.One2many(
        comodel_name='repair.order', inverse_name='sale_order_id',
        string='Repair Order', groups='stock.group_stock_user')
    repair_count = fields.Integer(
        "Repair Order(s)", compute='_compute_repair_count', groups='stock.group_stock_user')

    @api.depends('repair_order_ids')
    def _compute_repair_count(self):
        for order in self:
            order.repair_count = len(order.repair_order_ids)
    
    def action_show_repair(self):
        self.ensure_one()
        if self.repair_count == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "repair.order",
                "views": [[False, "form"]],
                "res_id": self.repair_order_ids.id,
            }
        elif self.repair_count > 1:
            return {
                "name": _("Repair Orders"),
                "type": "ir.actions.act_window",
                "res_model": "repair.order",
                "view_mode": "tree,form",
                "domain": [('sale_order_id', '=', self.id)],
            }

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def action_login_atelier(self):
        self.ensure_one()
        
        # On cible la vue Kanban des TUILES (pas des réparations)
        # Assurez-vous que l'ID xml 'view_atelier_dashboard_kanban' existe bien dans votre XML
        dashboard_view = self.env.ref('repair_custom.view_atelier_dashboard_kanban', raise_if_not_found=False)
        
        return {
            'name': _("Tableau de bord - %s") % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'atelier.dashboard.tile', 
            'view_mode': 'kanban',
            'view_id': dashboard_view.id if dashboard_view else False,
            'target': 'main',
            'context': {
                # C'est la seule chose qui compte ici : transmettre l'identité
                'atelier_employee_id': self.id, 
                'create': False, # Pas de bouton "Créer" sur le dashboard
            }
        }