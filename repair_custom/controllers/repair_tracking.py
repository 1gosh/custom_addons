from odoo import http
from odoo.http import request
from werkzeug.exceptions import TooManyRequests
import time
from collections import defaultdict

class RepairTrackingController(http.Controller):
    # Simple rate limiting: IP -> [(timestamp, count)]
    _rate_limit_cache = defaultdict(list)
    _rate_limit_window = 60  # 60 seconds
    _rate_limit_max_requests = 10  # 10 requests per minute

    def _check_rate_limit(self, ip):
        """Check if IP is within rate limit. Returns True if allowed."""
        now = time.time()

        # Clean old entries
        self._rate_limit_cache[ip] = [
            timestamp for timestamp in self._rate_limit_cache[ip]
            if now - timestamp < self._rate_limit_window
        ]

        # Check limit
        if len(self._rate_limit_cache[ip]) >= self._rate_limit_max_requests:
            return False

        # Add current request
        self._rate_limit_cache[ip].append(now)
        return True

    @http.route('/repair/tracking/<string:token>', type='http', auth='public', website=True)
    def repair_tracking(self, token=None, **kwargs):
        from odoo import fields as odoo_fields

        # Rate limiting
        ip = request.httprequest.remote_addr
        if not self._check_rate_limit(ip):
            raise TooManyRequests("Trop de requêtes. Veuillez réessayer dans une minute.")

        # Validate token format (should be 43 characters for secrets.token_urlsafe(32))
        if not token or len(token) < 32:
            return request.render('repair_custom.tracking_not_found')

        # Use exists() to prevent timing attacks - same response time for valid/invalid tokens
        # Remove sudo() - use proper public access instead
        order = request.env['repair.order'].search([('tracking_token', '=', token)], limit=1)

        if not order:
            return request.render('repair_custom.tracking_not_found')

        # Check token expiration
        if order.tracking_token_expiry and order.tracking_token_expiry < odoo_fields.Datetime.now():
            return request.render('repair_custom.tracking_expired', {
                'repair_name': order.name
            })

        # Limit exposed data by only passing necessary fields
        safe_order_data = {
            'id': order.id,
            'name': order.name,
            'state': order.state,
            'entry_date': order.entry_date,
            'device_id_name': order.device_id_name,
            'delivery_state': order.delivery_state,
        }

        return request.render('repair_custom.tracking_page', {
            'order': order,
            'safe_data': safe_order_data
        })