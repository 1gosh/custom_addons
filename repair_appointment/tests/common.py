from odoo.tests.common import TransactionCase


class RepairAppointmentCase(TransactionCase):
    """Shared fixture for repair_appointment tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Schedule = cls.env['repair.pickup.schedule']
        cls.Closure = cls.env['repair.pickup.closure']
        cls.Appointment = cls.env['repair.pickup.appointment']
        cls.Batch = cls.env['repair.batch']
        cls.Location = cls.env['repair.pickup.location']
        cls.Partner = cls.env['res.partner']

        cls.location_boutique = cls.Location.search([], limit=1)
        if not cls.location_boutique:
            cls.location_boutique = cls.Location.create({'name': 'Boutique Test'})
        cls.location_atelier = cls.Location.search(
            [('id', '!=', cls.location_boutique.id)], limit=1
        ) or cls.Location.create({'name': 'Atelier Test'})

        cls.partner = cls.Partner.create({
            'name': 'Client Test',
            'phone': '+33612345678',
            'email': 'client.test@example.com',
        })

    @classmethod
    def _make_batch(cls, partner=None, repair_count=2, location=None):
        partner = partner or cls.partner
        batch = cls.Batch.create({
            'partner_id': partner.id,
            'repair_ids': [
                (0, 0, {
                    'partner_id': partner.id,
                    'pickup_location_id': (location or cls.location_boutique).id,
                })
                for _ in range(repair_count)
            ],
        })
        return batch
