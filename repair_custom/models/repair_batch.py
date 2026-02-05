from odoo import api, Command, fields, models, _

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
