from odoo import api, fields, models


class WebsiteMonitorResult(models.AbstractModel):
    """Values stored from the last check (availability, SSL, domain)."""
    _name = 'website.monitor.result.mixin'
    _description = 'Website Monitor: Last Check Result'

    status = fields.Selection(
        [('no_checked', "Not Checked"), ('up', "Up"), ('down', "Down")],
        string="Status", default='no_checked', tracking=True, readonly=True)
    last_checked = fields.Datetime(string="Last Checked", readonly=True)
    response_time = fields.Float(string="Response Time (ms)", readonly=True)
    http_server = fields.Char(string="HTTP Server", readonly=True)
    http_status = fields.Integer(string="HTTP Status", readonly=True)
    http_status_msg = fields.Char(string="HTTP Status Message", readonly=True)
    ssl_status = fields.Char(string="SSL Status", readonly=True)
    ssl_issued_by = fields.Char(string="SSL Issued by", readonly=True)
    ssl_expiry_date = fields.Datetime(string="SSL Valid Until", readonly=True)
    ssl_days_left = fields.Integer(
        string="SSL Days Left", compute='_compute_days_left')
    domain_expiry_date = fields.Datetime(
        string="Domain Expires", readonly=True)
    domain_days_left = fields.Integer(
        string="Domain Days Left", compute='_compute_days_left')
    domain_status = fields.Char(string="Domain Check", readonly=True)
    domain_checked_at = fields.Datetime(readonly=True)

    @api.depends('ssl_expiry_date', 'domain_expiry_date')
    def _compute_days_left(self):
        """Days until the SSL certificate and the domain expire."""
        now = fields.Datetime.now()
        for monitor in self:
            monitor.ssl_days_left = (
                (monitor.ssl_expiry_date - now).days
                if monitor.ssl_expiry_date else 0)
            monitor.domain_days_left = (
                (monitor.domain_expiry_date - now).days
                if monitor.domain_expiry_date else 0)
