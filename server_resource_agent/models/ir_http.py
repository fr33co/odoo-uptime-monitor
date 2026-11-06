from odoo import models

from . import inflight


class IrHttp(models.AbstractModel):
    """Count the requests in flight, shared across worker processes."""
    _inherit = 'ir.http'

    @classmethod
    def _dispatch(cls, endpoint):
        """Count the request while it is being served (the agent's own route is excluded)."""
        if getattr(endpoint, 'routing', {}).get('agent_probe'):
            return super()._dispatch(endpoint)
        inflight.enter()
        try:
            return super()._dispatch(endpoint)
        finally:
            inflight.leave()
