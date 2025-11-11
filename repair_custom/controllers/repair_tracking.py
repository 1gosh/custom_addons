from odoo import http
from odoo.http import request

class RepairTrackingController(http.Controller):

    @http.route('/repair/tracking/<string:token>', type='http', auth='public', website=True)
    def repair_tracking(self, token=None, **kwargs):
        order = request.env['repair.order'].sudo().search([('tracking_token', '=', token)], limit=1)
        if not order:
            return request.render('repair_custom.tracking_not_found')
        return request.render('repair_custom.tracking_page', {'order': order})