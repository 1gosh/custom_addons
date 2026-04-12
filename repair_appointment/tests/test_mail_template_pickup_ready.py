# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'repair_appointment_mail')
class TestMailTemplatePickupReady(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({
            'name': 'Mme Test Mail',
            'email': 'mmetest@example.com',
        })
        self.batch = self.env['repair.batch'].create({
            'partner_id': self.partner.id,
        })
        self.repair_done = self.env['repair.order'].create({
            'partner_id': self.partner.id,
            'batch_id': self.batch.id,
            'internal_notes': 'Remplacement condensateur C12.',
        })
        self.repair_done.write({'state': 'done'})
        self.repair_irrep = self.env['repair.order'].create({
            'partner_id': self.partner.id,
            'batch_id': self.batch.id,
            'internal_notes': 'Carte mère HS, non réparable.',
        })
        self.repair_irrep.write({'state': 'irreparable'})
        self.apt = self.env['repair.pickup.appointment'].create({
            'batch_id': self.batch.id,
        })

    def test_body_contains_repair_summary(self):
        template = self.env.ref('repair_appointment.mail_template_pickup_ready')
        body = template._render_field('body_html', self.apt.ids)[self.apt.id]
        self.assertIn('Remplacement condensateur C12.', body)
        self.assertIn("Carte mère HS", body)
        self.assertIn("n'a pas pu être réparé", body)
        self.assertIn(self.partner.name, body)
        self.assertIn('Prendre rendez-vous', body)

    def test_build_pickup_quote_attachments_empty_without_sale(self):
        atts = self.batch._build_pickup_quote_attachments()
        self.assertEqual(atts, [])

    def _link_so_to_repair(self, repair, so):
        """Set sale_order_id on a repair bypassing the write guard."""
        self.env.cr.execute(
            "UPDATE repair_order SET sale_order_id = %s WHERE id = %s",
            (so.id, repair.id),
        )
        repair.invalidate_recordset(['sale_order_id'])

    def test_build_pickup_quote_attachments_skips_draft_so(self):
        so = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.env['product.product'].search([], limit=1).id,
                'name': 'Test',
                'product_uom_qty': 1,
                'price_unit': 10.0,
            })],
        })
        self._link_so_to_repair(self.repair_done, so)
        atts = self.batch._build_pickup_quote_attachments()
        self.assertEqual(atts, [])

    def test_build_pickup_quote_attachments_with_confirmed_so(self):
        product = self.env['product.product'].search([], limit=1)
        so = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': 'Test',
                'product_uom_qty': 1,
                'price_unit': 10.0,
            })],
        })
        self._link_so_to_repair(self.repair_done, so)
        so.action_confirm()
        self.assertEqual(so.state, 'sale')
        atts = self.batch._build_pickup_quote_attachments()
        self.assertEqual(len(atts), 1)
        attach = self.env['ir.attachment'].browse(atts[0])
        self.assertTrue(attach.exists())
        self.assertEqual(attach.mimetype, 'application/pdf')
