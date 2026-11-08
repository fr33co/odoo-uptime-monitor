{
    'name': 'Server Resource Agent',
    'version': '19.0.1.0.0',
    'summary': 'Expose CPU, memory, storage and concurrent requests to the uptime monitor',
    'description': """
Install this module on the Odoo server you want to monitor. It publishes a
read-only JSON endpoint, ``/server_agent/metrics``, protected by a token
(system parameter ``server_resource_agent.token``). The Server Uptime Monitor
polls it. Not available on Odoo Online / Odoo.sh, where modules that read the
host cannot be relied on.
""",
    'category': 'Tools',
    'author': 'Angel Guadarrama',
    'website': 'https://github.com/fr33co',
    'depends': ['base'],
    'external_dependencies': {'python': ['psutil']},
    'license': 'LGPL-3',
    'post_init_hook': '_post_init_hook',
    'installable': True,
    'application': False,
}
