import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from . import monitor_checks as checks

_logger = logging.getLogger(__name__)

MAX_WORKERS = 10


class WebsiteMonitor(models.Model):
    """A monitored website or TCP service: configuration and check orchestration.

    The cron runs every minute and checks the monitors whose ``next_check`` is
    due. Network I/O runs in worker threads through the check registry
    (:mod:`.monitor_checks`); results are written back from the main thread by
    :meth:`_apply_result`. The other responsibilities live in mixins: last
    result, incident state and notifications, server resources, history and
    uptime.
    """
    _name = 'website.monitor'
    _description = 'Website Uptime Monitor'
    _inherit = [
        'mail.thread',
        'website.monitor.result.mixin',
        'website.monitor.incident.mixin',
        'website.monitor.resources.mixin',
        'website.monitor.stats.mixin',
    ]
    _order = 'name, id'

    name = fields.Char(string="Website Name", required=True, tracking=True)
    url = fields.Char(
        string="URL / Host", required=True,
        help="Full URL for HTTP checks, or a host name for TCP checks.")
    active = fields.Boolean(default=True)
    monitor_group_id = fields.Many2one(
        'website.monitor.groups', string="Notify Group", required=True)
    show_on_status_page = fields.Boolean(string="Show on Status Page")

    # Check configuration
    check_type = fields.Selection(
        [('http', "HTTP"), ('tcp', "TCP port")],
        string="Check Type", default='http', required=True)
    tcp_port = fields.Integer(string="TCP Port")
    http_method = fields.Selection(
        [('GET', "GET"), ('HEAD', "HEAD")], string="Method",
        default='GET', required=True)
    expected_status = fields.Integer(
        string="Expected HTTP Status",
        help="Leave 0 to accept any status below 500.")
    expected_keyword = fields.Char(
        string="Expected Keyword",
        help="The response body must contain this text.")
    custom_headers = fields.Text(
        string="Custom Headers", help="One 'Name: value' per line.")
    auth_user = fields.Char(string="Basic Auth User")
    auth_password = fields.Char(
        string="Basic Auth Password", groups='server_uptime_monitor.group_website_monitor_manager')
    verify_ssl = fields.Boolean(string="Verify SSL", default=True)
    is_odoo = fields.Boolean(
        string="Odoo Instance",
        help="Measure responsiveness the way an Odoo.sh instance needs: one "
             "anonymous GET to /web/login (rendered by a worker, needs the "
             "database), timing only the response headers and not "
             "downloading the page. Combine with 'Slow Threshold' and "
             "'Failures Before Alert' to be warned only when the instance "
             "stays slow. Keep the interval at 5 minutes or more.")
    check_domain_expiry = fields.Boolean(string="Check Domain Expiry")
    check_interval = fields.Integer(
        string="Check Every (min)", default=5, required=True)
    timeout = fields.Integer(string="Timeout (s)", default=10, required=True)
    failure_threshold = fields.Integer(
        string="Failures Before Alert", default=2, required=True,
        help="Consecutive failed checks needed before alerting.")
    max_response_time = fields.Float(
        string="Slow Threshold (ms)", default=4000,
        help="A response slower than this triggers an alert.")
    next_check = fields.Datetime(string="Next Check", readonly=True, index=True)

    color = fields.Integer(string="Color", compute='_compute_color')

    _check_interval_positive = models.Constraint(
        'CHECK(check_interval > 0)', "The check interval must be positive.")
    _timeout_positive = models.Constraint(
        'CHECK(timeout > 0)', "The timeout must be positive.")
    _threshold_positive = models.Constraint(
        'CHECK(failure_threshold > 0)',
        "The failure threshold must be at least 1.")

    @api.constrains('check_type', 'tcp_port')
    def _check_tcp_port(self):
        """A TCP monitor needs a valid port."""
        for monitor in self:
            if monitor.check_type == 'tcp' and not 0 < monitor.tcp_port < 65536:
                raise ValidationError(
                    self.env._("A valid TCP port is required."))

    @api.onchange('is_odoo')
    def _onchange_is_odoo(self):
        """Server resources only make sense for an Odoo server."""
        if not self.is_odoo:
            self.agent_enabled = False

    @api.depends(
        'status', 'response_time', 'max_response_time', 'ssl_status',
        'agent_status', 'cpu_percent', 'memory_percent', 'disk_percent',
        'requests_in_flight', 'max_cpu', 'max_memory', 'max_disk',
        'max_concurrent', 'db_expiry_date')
    def _compute_color(self):
        """Kanban color: red if down, yellow on warnings, green if healthy, none if never checked.

        Odoo color indexes: 1 red, 3 yellow, 10 green.
        """
        for monitor in self:
            if monitor.status == 'down':
                monitor.color = 1
            elif monitor.status == 'no_checked':
                monitor.color = 0
            elif (monitor._has_problem() or monitor.ssl_status == "Expiring soon"
                    or monitor._db_expiring()):
                monitor.color = 3
            else:
                monitor.color = 10

    def _extra_problems(self):
        """Hook of the incident mixin: resource thresholds exceeded."""
        return self._resource_problems()

    def _expiry_sources(self):
        """Hook of the incident mixin: add the database validity."""
        return super()._expiry_sources() + self._db_expiry_sources()

    # ------------------------------------------------------------
    # CHECKS
    # ------------------------------------------------------------

    def _check_params(self):
        """Snapshot the settings needed by a check into a plain dict.

        Worker threads must not touch the ORM, so everything they need
        (including the basic auth password, read with sudo because it is
        restricted to managers) is read here, in the main thread.
        """
        self.ensure_one()
        sudo = self.sudo()
        auth = ((sudo.auth_user, sudo.auth_password or '')
                if sudo.auth_user else None)
        return dict({
            'url': self.url,
            'check_type': self.check_type,
            'port': self.tcp_port,
            'method': self.http_method,
            'timeout': self.timeout,
            'odoo': self.is_odoo,
            'verify_ssl': self.verify_ssl,
            'headers': checks.parse_headers(self.custom_headers),
            'auth': auth,
            'expected_status': self.expected_status,
            'keyword': self.expected_keyword,
            'check_domain': self.check_domain_expiry and (
                not self.domain_checked_at
                or self.domain_checked_at < fields.Datetime.now() - timedelta(days=1)),
        }, **self._agent_params())

    @api.model
    def _run_checks(self, params):
        """Run the registered network checks for ``params`` (thread-safe)."""
        return checks.run_all(params)

    def check_http_status(self):
        """Run the availability check of each monitor (kept for buttons)."""
        self._check_now_all()

    def check_ssl_certificate(self):
        """Button/legacy entry point: run the checks of each record now."""
        self._check_now_all()

    def _check_now_all(self):
        """Run and apply the checks of each record synchronously, without writing history."""
        for monitor in self:
            monitor._apply_result(self._run_checks(monitor._check_params()))

    @api.model
    def check_website_monitor_cron(self):
        """Cron entry point: check every monitor that is due, in parallel.

        Network checks run in a thread pool; results are applied and history is
        created afterwards in this thread. A failure on one monitor is logged
        and does not stop the others.
        """
        now = fields.Datetime.now()
        monitors = self.search([
            '|', ('next_check', '=', False), ('next_check', '<=', now)])
        if not monitors:
            return
        params = {m.id: m._check_params() for m in monitors}
        # Network I/O in threads; ORM writes stay in this thread.
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                m.id: pool.submit(self._run_checks, params[m.id])
                for m in monitors}
        history_vals = []
        for monitor in monitors:
            try:
                monitor._apply_result(futures[monitor.id].result())
                history_vals.append(monitor._prepare_history_vals())
            except Exception:  # one broken site must not stop the others
                _logger.exception("Monitoring failed for %s", monitor.url)
        self.env['website.monitor.history'].create(history_vals)

    # ------------------------------------------------------------
    # ACTIONS
    # ------------------------------------------------------------

    def _prepare_history_vals(self):
        """Values of the history row describing the last check."""
        self.ensure_one()
        return dict({
            'server_id': self.id,
            'last_checked': self.last_checked,
            'response_time': self.response_time,
            'http_status': self.http_status,
            'status': self.status,
        }, **self._resource_history_vals())

    def action_check_now(self):
        """'Check Now' button: check immediately and record a history row."""
        self._check_now_all()
        self.env['website.monitor.history'].create(
            [monitor._prepare_history_vals() for monitor in self])

    def action_view_history(self):
        """'History' smart button: open the history of this monitor, last 24 hours first."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'server_uptime_monitor.website_monitor_history_action')
        action['domain'] = [('server_id', '=', self.id)]
        action['context'] = {
            'default_server_id': self.id,
            'search_default_last_24h': 1,
        }
        return action
