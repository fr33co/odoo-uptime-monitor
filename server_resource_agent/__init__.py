import secrets

from . import controllers
from . import models

TOKEN_PARAM = 'server_resource_agent.token'


def _post_init_hook(env):
    """Generate the access token on first install."""
    params = env['ir.config_parameter'].sudo()
    if not params.get_param(TOKEN_PARAM):
        params.set_param(TOKEN_PARAM, secrets.token_urlsafe(32))
