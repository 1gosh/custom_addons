from datetime import date
from odoo import http
from odoo.exceptions import UserError
from odoo.http import request


class RepairPickupPortal(http.Controller):

    def _get_appointment(self, token):
        apt = request.env['repair.pickup.appointment'].sudo().search(
            [('token', '=', token)], limit=1,
        )
        return apt or False

    @http.route('/my/pickup/<string:token>', type='http', auth='public',
                website=True)
    def pickup_landing(self, token, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return request.render('repair_appointment.portal_pickup_page', {
            'apt': apt,
        })

    @http.route('/my/pickup/<string:token>/slots', type='json', auth='public')
    def pickup_slots(self, token, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return []
        days = apt._compute_available_days(apt.location_id)
        return [
            {
                'date': d['date'].isoformat(),
                'state': d['state'],
                'remaining_capacity': d['remaining_capacity'],
            }
            for d in days
        ]

    # csrf=False: UUID4 token in URL is the auth mechanism
    @http.route('/my/pickup/<string:token>/book', type='http', auth='public',
                methods=['POST'], csrf=False, website=True)
    def pickup_book(self, token, pickup_date=None, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return self._schedule_from_form(apt, pickup_date, expected_state='pending')

    # csrf=False: UUID4 token in URL is the auth mechanism
    @http.route('/my/pickup/<string:token>/reschedule', type='http',
                auth='public', methods=['POST'], csrf=False, website=True)
    def pickup_reschedule(self, token, pickup_date=None, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return self._schedule_from_form(apt, pickup_date, expected_state='scheduled')

    @http.route('/my/pickup/<string:token>/confirmation', type='http',
                auth='public', website=True)
    def pickup_confirmation(self, token, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return request.render(
            'repair_appointment.portal_pickup_confirmation', {'apt': apt},
        )

    @http.route('/my/pickup/<string:token>/compare', type='http',
                auth='public', website=True)
    def pickup_compare(self, token, **kwargs):
        """Temporary comparison page: native date input vs Flatpickr."""
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return request.render(
            'repair_appointment.portal_pickup_compare', {'apt': apt},
        )

    # ----- helpers -----

    def _schedule_from_form(self, apt, date_iso, expected_state):
        if not date_iso:
            return self._render_error(apt, "Date de retrait manquante.")
        try:
            pickup_date = date.fromisoformat(date_iso)
        except ValueError:
            return self._render_error(apt, "Format de date invalide.")

        if apt.state != expected_state:
            return self._render_error(
                apt, "Ce rendez-vous ne peut plus être modifié ici."
            )

        try:
            apt.sudo().with_context(portal_booking=True).action_schedule(pickup_date)
        except UserError as e:
            return self._render_error(apt, str(e))

        apt.sudo().message_post(body=(
            "RDV %s par le client depuis le portail (IP: %s)."
        ) % (
            'replanifié' if expected_state == 'scheduled' else 'pris',
            request.httprequest.remote_addr or '?',
        ))

        return request.redirect(f'/my/pickup/{apt.token}/confirmation')

    def _render_error(self, apt, message):
        return request.render('repair_appointment.portal_pickup_page', {
            'apt': apt,
            'error': message,
        })
