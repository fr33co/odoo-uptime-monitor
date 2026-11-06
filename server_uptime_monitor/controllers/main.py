from odoo import http
from odoo.http import request


class WebsiteMonitorStatusPage(http.Controller):
    """Public status page listing the monitors flagged for it."""

    @http.route('/status', type='http', auth='public')
    def status_page(self, **kwargs):
        """Render ``/status``.

        Runs as sudo but exposes only name, status and 30-day uptime, never the URL.
        """
        monitors = request.env['website.monitor'].sudo().search(
            [('show_on_status_page', '=', True)])
        return request.render('server_uptime_monitor.status_page', {
            'monitors': monitors,
            'all_up': all(m.status == 'up' for m in monitors),
        })
