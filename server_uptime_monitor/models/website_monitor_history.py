from datetime import timedelta

from odoo import api, fields, models


class WebsiteMonitorHistory(models.Model):
    """One row per check, feeding the graphs, the uptime figures and the history views."""
    _name = 'website.monitor.history'
    _description = 'Website Uptime Monitor History'
    _order = 'last_checked desc, id desc'

    server_id = fields.Many2one(
        'website.monitor', string="Server", required=True,
        ondelete='cascade', index=True)
    last_checked = fields.Datetime(string="Last Checked", index=True)
    response_time = fields.Float(
        string="Response Time (ms)", aggregator='avg')
    http_status = fields.Integer(string="HTTP Status")
    cpu_percent = fields.Float(string="CPU (%)", aggregator='avg')
    memory_percent = fields.Float(string="Memory (%)", aggregator='avg')
    disk_percent = fields.Float(string="Storage (%)", aggregator='avg')
    requests_in_flight = fields.Integer(
        string="Concurrent Requests", aggregator='max')
    check_label = fields.Char(
        string="Check Time (UTC)", compute='_compute_check_label', store=True,
        help="Check time truncated to the minute. Sortable text used to plot"
             " one point per check in graphs.")
    status = fields.Selection(
        [('up', "Up"), ('down', "Down")], string="Status")

    @api.depends('last_checked')
    def _compute_check_label(self):
        """Store the check time truncated to the minute (UTC) as sortable text, used to plot one point per check."""
        for history in self:
            history.check_label = (
                history.last_checked.strftime('%Y-%m-%d %H:%M')
                if history.last_checked else False)

    @api.autovacuum
    def _gc_old_history(self):
        """Drop history older than the configured retention (days)."""
        days = int(self.env['ir.config_parameter'].sudo().get_param(
            'server_uptime_monitor.history_retention_days', 90))
        limit = fields.Datetime.now() - timedelta(days=days)
        self.search([('last_checked', '<', limit)]).unlink()
