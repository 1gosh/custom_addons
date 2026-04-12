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
