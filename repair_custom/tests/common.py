# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase


class RepairQuoteCase(TransactionCase):
    """Shared fixture for repair_custom quote lifecycle tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Repair = cls.env['repair.order']
        cls.SaleOrder = cls.env['sale.order']
        cls.Activity = cls.env['mail.activity']
        cls.Partner = cls.env['res.partner']
        cls.Employee = cls.env['hr.employee']
        cls.User = cls.env['res.users']
        cls.Product = cls.env['product.product']

        # Config parameters — set to known values
        cls.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.quote_reminder_delay_days', '5'
        )
        cls.env['ir.config_parameter'].sudo().set_param(
            'repair_custom.quote_escalation_delay_days', '3'
        )

        # Client partner
        cls.partner = cls.Partner.create({
            'name': 'Client Quote Test',
            'email': 'client.quote@example.com',
            'phone': '+33611112222',
        })

        # Manager group
        cls.manager_group = cls.env.ref('repair_custom.group_repair_manager')
        # Sales group — managers also need to write to sale.order when the
        # quote lifecycle sync is exercised from the sale.order side.
        cls.sales_group = cls.env.ref('sales_team.group_sale_salesman_all_leads')

        # Ensure the test runner user can write protected fields on repair.order
        # (e.g. sale_order_id inverse write triggered by sale.order creation).
        cls.env.user.groups_id = [(4, cls.manager_group.id), (4, cls.sales_group.id)]

        # Two manager users for escalation activity tests
        cls.manager_user_1 = cls.User.create({
            'name': 'Manager One',
            'login': 'manager1_quote_test@example.com',
            'email': 'manager1_quote_test@example.com',
            'groups_id': [(6, 0, [cls.manager_group.id, cls.sales_group.id])],
        })
        cls.manager_user_2 = cls.User.create({
            'name': 'Manager Two',
            'login': 'manager2_quote_test@example.com',
            'email': 'manager2_quote_test@example.com',
            'groups_id': [(6, 0, [cls.manager_group.id, cls.sales_group.id])],
        })

        # Tech with Odoo user
        cls.tech_user = cls.User.create({
            'name': 'Tech With Account',
            'login': 'tech_user_quote_test@example.com',
            'email': 'tech_user_quote_test@example.com',
        })
        cls.tech_with_user = cls.Employee.create({
            'name': 'Tech With Account',
            'user_id': cls.tech_user.id,
        })

        # Tech without Odoo user
        cls.tech_without_user = cls.Employee.create({
            'name': 'Tech Without Account',
        })

        # A service product for the pricing wizard path (if needed)
        cls.service_product = cls.Product.search([
            ('type', '=', 'service'),
            ('default_code', '=', 'SERV'),
        ], limit=1)
        if not cls.service_product:
            cls.service_product = cls.Product.create({
                'name': 'Service Réparation Test',
                'default_code': 'SERV',
                'type': 'service',
                'list_price': 50.0,
            })

    @classmethod
    def _make_repair(cls, tech=None, internal_notes='Notes de diagnostic', quote_required=True):
        tech = tech or cls.tech_with_user
        return cls.Repair.create({
            'partner_id': cls.partner.id,
            'internal_notes': internal_notes,
            'quote_required': quote_required,
            'technician_employee_id': tech.id,
        })

    @classmethod
    def _make_sale_order_linked(cls, repair):
        """Create a sale.order linked to a repair (minimal, bypasses pricing wizard)."""
        so = cls.SaleOrder.create({
            'partner_id': cls.partner.id,
            'repair_order_ids': [(4, repair.id)],
            'order_line': [(0, 0, {
                'product_id': cls.service_product.id,
                'name': 'Service forfait test',
                'product_uom_qty': 1.0,
                'price_unit': 100.0,
            })],
        })
        repair.sale_order_id = so.id
        return so
