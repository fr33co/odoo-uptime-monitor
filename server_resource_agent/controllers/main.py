import hmac
import ipaddress
import json

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request

from ..models import metrics


def _ip_allowed(allowed, remote):
    """Whether ``remote`` is in the comma-separated list of IPs/CIDRs (empty list allows all)."""
    networks = [x.strip() for x in (allowed or '').split(',') if x.strip()]
    if not networks:
        return True
    try:
        address = ipaddress.ip_address(remote)
        return any(address in ipaddress.ip_network(n, strict=False)
                   for n in networks)
    except ValueError:
        return False


class ResourceAgent(http.Controller):
    """Token-protected, read-only metrics endpoint polled by the Server Uptime Monitor."""

    @http.route('/server_agent/metrics', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False, agent_probe=True)
    def metrics(self, **_kwargs):
        """Return the server metrics as JSON to callers that present the token.

        If the system parameter ``server_resource_agent.allowed_ips`` is set
        (comma-separated IPs or CIDRs), other addresses get a 404 as well.
        Behind a reverse proxy enable ``proxy_mode`` so the real client IP is used.
        Answers 404 to everyone else so the endpoint is not advertised.
        """
        params = request.env['ir.config_parameter'].sudo()
        if not _ip_allowed(
                params.get_str('server_resource_agent.allowed_ips'),
                request.httprequest.remote_addr):
            raise NotFound()
        expected = params.get_str('server_resource_agent.token')
        header = request.httprequest.headers.get('Authorization', '')
        given = header[7:] if header.startswith('Bearer ') else (
            request.httprequest.headers.get('X-Agent-Token', ''))
        if not expected or not hmac.compare_digest(
                given.encode(), expected.encode()):
            raise NotFound()
        body = json.dumps(metrics.collect(request.env.cr))
        return request.make_response(body, headers=[
            ('Content-Type', 'application/json'),
            ('Cache-Control', 'no-store')])
