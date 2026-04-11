from odoo.tests.common import TransactionCase


class RepairAppointmentCase(TransactionCase):
    """Shared fixture for repair_appointment tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Schedule = cls.env['repair.pickup.schedule']
        cls.Location = cls.env['repair.pickup.location']
        # Pick (or create) two distinct locations for tests
        cls.location_boutique = cls.Location.search([], limit=1)
        if not cls.location_boutique:
            cls.location_boutique = cls.Location.create({'name': 'Boutique Test'})
        cls.location_atelier = cls.Location.search(
            [('id', '!=', cls.location_boutique.id)], limit=1
        ) or cls.Location.create({'name': 'Atelier Test'})
