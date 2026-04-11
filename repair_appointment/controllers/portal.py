from datetime import datetime
from odoo import http, fields
from odoo.exceptions import UserError
from odoo.http import request


class RepairPickupPortal(http.Controller):

    def _get_appointment(self, token):
        """Look up an appointment by token. Returns a record or False."""
        apt = request.env['repair.pickup.appointment'].sudo().search(
            [('token', '=', token)], limit=1,
        )
        return apt or False

    @http.route('/my/pickup/<string:token>', type='http', auth='public', website=True)
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
        slots = apt._compute_available_slots(apt.location_id)
        return [
            {
                'start': s['datetime_start'].isoformat(),
                'end': s['datetime_end'].isoformat(),
                'label': s['datetime_start'].strftime('%A %d %B %Y %H:%M'),
                'remaining': s['remaining_capacity'],
            }
            for s in slots
        ]

    # csrf=False: UUID4 token in URL is the auth mechanism
    @http.route('/my/pickup/<string:token>/book', type='http', auth='public',
                methods=['POST'], csrf=False, website=True)
    def pickup_book(self, token, start_datetime=None, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return self._schedule_from_form(apt, start_datetime, expected_state='pending')

    # csrf=False: UUID4 token in URL is the auth mechanism
    @http.route('/my/pickup/<string:token>/reschedule', type='http', auth='public',
                methods=['POST'], csrf=False, website=True)
    def pickup_reschedule(self, token, start_datetime=None, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return self._schedule_from_form(apt, start_datetime, expected_state='scheduled')

    @http.route('/my/pickup/<string:token>/confirmation', type='http', auth='public',
                website=True)
    def pickup_confirmation(self, token, **kwargs):
        apt = self._get_appointment(token)
        if not apt:
            return request.not_found()
        return request.render('repair_appointment.portal_pickup_confirmation', {
            'apt': apt,
        })

    # ----- helpers -----

    def _schedule_from_form(self, apt, start_iso, expected_state):
        if not start_iso:
            return self._render_error(apt, "Créneau manquant.")
        try:
            start = datetime.fromisoformat(start_iso)
        except ValueError:
            return self._render_error(apt, "Format de date invalide.")

        if apt.state != expected_state:
            return self._render_error(apt, "Ce rendez-vous ne peut plus être modifié ici.")

        schedule = request.env['repair.pickup.schedule'].sudo().search(
            [('location_id', '=', apt.location_id.id)], limit=1,
        )
        if not schedule:
            return self._render_error(apt, "Configuration de créneaux manquante.")

        # End = start + duration of slot1 or slot2 (whichever matches)
        duration_hours = schedule.slot1_end - schedule.slot1_start
        if abs(start.hour + start.minute / 60 - schedule.slot2_start) < 0.01:
            duration_hours = schedule.slot2_end - schedule.slot2_start
        from datetime import timedelta
        end = start + timedelta(hours=int(duration_hours),
                                minutes=int((duration_hours % 1) * 60))

        try:
            apt.sudo().with_context(portal_booking=True).action_schedule(start, end)
        except UserError as e:
            return self._render_error(apt, str(e))

        apt.sudo().message_post(body=(
            "RDV %s par le client depuis le portail (IP: %s)."
        ) % ('replanifié' if expected_state == 'scheduled' else 'pris',
             request.httprequest.remote_addr or '?'))

        return request.redirect(f'/my/pickup/{apt.token}/confirmation')

    def _render_error(self, apt, message):
        return request.render('repair_appointment.portal_pickup_page', {
            'apt': apt,
            'error': message,
        })
