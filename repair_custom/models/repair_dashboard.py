import time
from datetime import datetime, date
from datetime import time as dt_time

from odoo import api, Command, fields, models, _

class AtelierDashboardTile(models.Model):
    _name = 'atelier.dashboard.tile'
    _description = 'Tuile du Tableau de bord Atelier'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    name = fields.Char("Titre", required=True)
    color = fields.Integer("Couleur")
    category_type = fields.Selection([
        ('todo', 'À faire'),
        ('progress', 'En cours (Moi)'),
        ('waiting', 'Attente de pièces'),
        ('quote_waiting', 'Devis en attente'),
        ('quote_validated', 'Devis validé'),
        ('today', 'Activité du jour'),
        ('done', 'Terminées'),
    ], string="Type de catégorie", required=True)

    count_reparations = fields.Integer(compute='_compute_count', string="Nombre")

    # Cache for dashboard counts (class-level cache with TTL)
    _dashboard_cache = {}
    _cache_ttl = 30  # Cache for 30 seconds

    def _get_cache_key(self, employee_id, current_uid):
        """Generate cache key for current context."""
        return (self.id, employee_id or 0, current_uid, int(time.time() / self._cache_ttl))

    def _compute_count(self):
        Reparation = self.env['repair.order']
        employee_id = self._context.get('atelier_employee_id')
        current_uid = self.env.uid

        # Batch optimization: Collect all domains and execute efficiently
        # Clear old cache entries (older than 60 seconds)
        current_time = int(time.time())
        # Modify cache in place instead of reassigning (Odoo doesn't allow reassigning class attributes)
        cache = type(self)._dashboard_cache
        expired_keys = [k for k in list(cache.keys()) if k[3] <= (current_time / self._cache_ttl) - 2]
        for key in expired_keys:
            cache.pop(key, None)
        
        for record in self:
            domain = []
            
            # --- 1. Filtre À FAIRE ---
            if record.category_type == 'todo':
                domain = [('state', '=', 'confirmed')]
                
            # --- 2. Filtre EN COURS (Logique Kiosque) ---
            elif record.category_type == 'progress':
                domain = [
                    ('state', '=', 'under_repair'),
                    ('quote_state', '!=', 'pending')
                ]
                # Si on est en mode Kiosque (Pierre est là), on compte SES réparations
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                # Sinon (Admin classique), on compte celles de son user
                else:
                    domain.append(('user_id', '=', self.env.uid))

            # --- 3. Autres filtres ---
            elif record.category_type == 'waiting':
                domain = [('parts_waiting', '=', True)]
                # AJOUT DU FILTRE PROPRIÉTAIRE
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', current_uid))
            elif record.category_type == 'quote_waiting':
                domain = [
                    ('state', '=', 'under_repair'), 
                    ('quote_state', '=', 'pending')
                ]
                # AJOUT DU FILTRE PROPRIÉTAIRE
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', current_uid))
            elif record.category_type == 'quote_validated':
                domain = [
                    ('state', '=', 'under_repair'), 
                    ('quote_state', '=', 'approved')
                ]
                # AJOUT DU FILTRE PROPRIÉTAIRE
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', current_uid))
            elif record.category_type == 'today':
                today_start = datetime.combine(date.today(), dt_time.min)
                # Réparations modifiées aujourd'hui PAR le technicien
                domain = [('write_date', '>=', today_start)]
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', self.env.uid))
            elif record.category_type == 'done':
                domain = [('state', '=', 'done')]
                # Filtre Propriétaire
                if employee_id:
                    domain.append(('technician_employee_id', '=', employee_id))
                else:
                    domain.append(('user_id', '=', current_uid))
            
            # Sécurité globale sur les compteurs (pas d'annulés)
            domain.append(('state', '!=', 'cancel'))

            # Pour les tuiles de travail (todo/waiting), on ne veut pas les brouillons accidentels
            if record.category_type in ['todo', 'waiting']:
                 domain.append(('state', '!=', 'draft'))

            # Check cache first
            cache_key = record._get_cache_key(employee_id, current_uid)
            cache = type(self)._dashboard_cache
            if cache_key in cache:
                record.count_reparations = cache[cache_key]
            else:
                # Perform count and cache result
                count = Reparation.search_count(domain)
                cache[cache_key] = count
                record.count_reparations = count

    def _get_category_config(self):
        """Extract category configuration to reduce complexity."""
        today_start = datetime.combine(date.today(), dt_time.min)

        # Configuration mapping for each category type
        category_configs = {
            'todo': {
                'search_defaults': {'search_default_todo': 1},
                'domain_filters': [('state', '!=', 'draft')],
            },
            'progress': {
                'search_defaults': {'search_default_in_progress': 1, 'search_default_my_session': 1},
                'extra_context': lambda ctx: {'default_technician_employee_id': ctx.get('atelier_employee_id')} if ctx.get('atelier_employee_id') else {},
            },
            'waiting': {
                'search_defaults': {'search_default_parts': 1, 'search_default_my_session': 1},
            },
            'quote_waiting': {
                'search_defaults': {'search_default_quote_waiting': 1, 'search_default_my_session': 1},
            },
            'quote_validated': {
                'search_defaults': {'search_default_quote_validated': 1, 'search_default_my_session': 1},
            },
            'done': {
                'search_defaults': {'search_default_done': 1, 'search_default_my_session': 1},
            },
            'today': {
                'search_defaults': {'search_default_my_session': 1},
                'domain_filters': [('write_date', '>=', today_start)],
                'custom_views': True,
            },
        }

        return category_configs.get(self.category_type, {})

    def action_open_reparations(self):
        """Open repairs list view filtered by category type."""
        self.ensure_one()

        action = self.env['ir.actions.act_window']._for_xml_id('repair_custom.action_repair_order_atelier')
        ctx = self._context.copy()
        domain = [('state', 'not in', ['draft', 'cancel'])]

        # Get configuration for this category
        config = self._get_category_config()

        # Apply search defaults
        if 'search_defaults' in config:
            ctx.update(config['search_defaults'])

        # Apply extra context (callable)
        if 'extra_context' in config:
            extra = config['extra_context'](ctx)
            ctx.update(extra)

        # Apply domain filters
        if 'domain_filters' in config:
            domain.extend(config['domain_filters'])

        # Handle custom views (today/history case)
        if config.get('custom_views'):
            history_view = self.env.ref('repair_custom.view_repair_order_atelier_history_tree', raise_if_not_found=False)
            if history_view:
                action['views'] = [
                    (history_view.id, 'tree'),
                    (self.env.ref('repair_custom.view_repair_order_atelier_kanban').id, 'kanban'),
                    (self.env.ref('repair_custom.view_repair_order_atelier_form').id, 'form'),
                ]

        action['domain'] = domain
        action['context'] = ctx
        action['name'] = self.name

        return action