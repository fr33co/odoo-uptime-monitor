from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .monitor_incident import parse_days

from . import monitor_checks as checks

DEFAULT_DB_EXPIRY_DAYS = "5"
METRICS = ('response_time', 'cpu_percent', 'memory_percent', 'disk_percent',
           'requests_in_flight')


class WebsiteMonitorResources(models.AbstractModel):
    """Optional server resources (CPU, memory, storage, concurrency) read from the agent.

    Only meaningful for Odoo servers; the host model must provide ``is_odoo``,
    ``check_type``, ``url`` and ``timeout``.
    """
    _name = 'website.monitor.resources.mixin'
    _description = 'Website Monitor: Server Resources'

    agent_enabled = fields.Boolean(
        string="Server Resources",
        help="Only for Odoo servers. Read CPU, memory, storage and concurrent "
             "requests from the Server Resource Agent module installed on the "
             "monitored server.")
    agent_url = fields.Char(
        string="Agent URL",
        help="Leave empty to use <site origin>/server_agent/metrics.")
    agent_token = fields.Char(
        string="Agent Token",
        groups='server_uptime_monitor.group_website_monitor_manager',
        help="System parameter server_resource_agent.token of the server.")
    db_estimate_days = fields.Integer(
        string="Estimate Expiry After (days)", default=30,
        help="If the database reports no expiration date, assume it expires "
             "this many days after its creation, and warn accordingly. "
             "0 disables the estimate.")
    db_alert_days = fields.Char(
        string="Warn Database Expiry (days)", default=DEFAULT_DB_EXPIRY_DAYS,
        help="Days before the Odoo database expires at which the group is "
             "warned, comma-separated (e.g. 30,15,7,5,1). One warning per "
             "level; add 1 to be warned again on the last day.")
    max_cpu = fields.Float(string="Max CPU (%)", default=90)
    max_memory = fields.Float(string="Max Memory (%)", default=90)
    max_disk = fields.Float(string="Max Storage (%)", default=90)
    max_concurrent = fields.Integer(
        string="Max Concurrent Requests", default=0,
        help="Peak of requests served at the same time between two checks. "
             "0 disables this threshold.")

    agent_status = fields.Char(string="Agent Status", readonly=True)
    db_expiry_date = fields.Datetime(
        string="Database Valid Until", readonly=True,
        help="Expiration date of the Odoo database (demo, trial or "
             "subscription), as reported by the agent.")
    db_expiry_estimated = fields.Boolean(
        string="Estimated Expiry", readonly=True,
        help="The date is an estimate (creation date plus the configured days), "
             "not reported by the database.")
    db_create_date = fields.Datetime(string="Database Created", readonly=True)
    db_expiry_reason = fields.Char(string="Database Validity Type", readonly=True)
    db_days_left = fields.Integer(
        string="Database Days Left", compute='_compute_db_days_left',
        help="Negative once the database has expired.")
    db_alert_level = fields.Integer(readonly=True)
    cpu_percent = fields.Float(string="CPU (%)", readonly=True)
    memory_percent = fields.Float(string="Memory (%)", readonly=True)
    disk_percent = fields.Float(string="Storage (%)", readonly=True)
    requests_in_flight = fields.Integer(
        string="Concurrent Requests", readonly=True)

    @api.constrains('db_alert_days')
    def _check_db_alert_days(self):
        """The warning days must be a comma-separated list of whole numbers."""
        for monitor in self:
            try:
                parse_days(monitor.db_alert_days, DEFAULT_DB_EXPIRY_DAYS)
            except ValueError:
                raise ValidationError(self.env._(
                    "Warning days must be whole numbers separated by commas, "
                    "e.g. 30,15,7,1."))

    @api.depends('db_expiry_date')
    def _compute_db_days_left(self):
        """Days until the database expires (negative when it already has)."""
        now = fields.Datetime.now()
        for monitor in self:
            monitor.db_days_left = (
                (monitor.db_expiry_date - now).days
                if monitor.db_expiry_date else 0)

    def _db_expiry_sources(self):
        """Expiry source of the database validity, for the incident mixin."""
        self.ensure_one()
        if not (self.agent_enabled and self.is_odoo):
            return []
        return [('db_expiry_date', 'db_alert_level', 'db_expiry',
                 parse_days(self.db_alert_days, DEFAULT_DB_EXPIRY_DAYS))]

    def _db_expiring(self):
        """Whether the database expires within a week (or already has)."""
        self.ensure_one()
        return bool(self.agent_enabled and self.is_odoo and self.db_expiry_date
                    and self.db_days_left <= 7)

    @api.constrains('agent_enabled', 'agent_url', 'check_type')
    def _check_agent_url(self):
        """A TCP monitor has no site URL to derive the agent address from."""
        for monitor in self:
            if (monitor.agent_enabled and monitor.check_type == 'tcp'
                    and not monitor.agent_url):
                raise ValidationError(
                    self.env._("Set the Agent URL for a TCP monitor."))

    @api.constrains('agent_url')
    def _check_agent_url_scheme(self):
        """The agent token travels in a header: refuse plain HTTP except to loopback."""
        for monitor in self.filtered('agent_url'):
            if not checks.is_secure_agent_url(monitor.agent_url):
                raise ValidationError(self.env._(
                    "The Agent URL must use https:// (the token is sent in a header)."))

    def _agent_params(self):
        """Check parameters of the resource check (read here, in the main thread)."""
        self.ensure_one()
        return {
            'agent': self.agent_enabled and self.is_odoo,
            'agent_url': self.agent_url or (
                checks.agent_url(self.url) if self.check_type == 'http'
                else False),
            'agent_token': self.sudo().agent_token or '',
            'db_estimate_days': self.db_estimate_days,
        }

    def _resource_history_vals(self):
        """Resource values to store in the history row of the last check."""
        self.ensure_one()
        return {
            'cpu_percent': self.cpu_percent,
            'memory_percent': self.memory_percent,
            'disk_percent': self.disk_percent,
            'requests_in_flight': self.requests_in_flight,
        }

    def _resource_problems(self):
        """Human-readable list of the resource thresholds currently exceeded."""
        self.ensure_one()
        if not (self.agent_enabled and self.is_odoo) or self.agent_status != "OK":
            return []
        problems = []
        for label, value, limit in (
                ("CPU", self.cpu_percent, self.max_cpu),
                ("Memory", self.memory_percent, self.max_memory),
                ("Storage", self.disk_percent, self.max_disk),
                ("Concurrent requests", self.requests_in_flight,
                 self.max_concurrent)):
            if limit and value > limit:
                problems.append("%s: %s (max %s)" % (label, value, limit))
        return problems

    def action_view_metric(self):
        """Smart buttons: line chart over time of one metric (``metric`` in the context)."""
        self.ensure_one()
        metric = self.env.context.get('metric')
        if metric not in METRICS:
            return self.action_view_history()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'server_uptime_monitor.website_monitor_history_action')
        action['name'] = self.env['website.monitor.history']._fields[
            metric]._description_string(self.env)
        action['domain'] = [('server_id', '=', self.id)]
        action['view_mode'] = 'graph,list,pivot'
        action['context'] = {
            'graph_measure': metric,
            'graph_mode': 'line',
            'search_default_last_24h': 1,
        }
        return action
