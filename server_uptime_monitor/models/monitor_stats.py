from datetime import timedelta

from odoo import fields, models


class WebsiteMonitorStats(models.AbstractModel):
    """History of a monitor and its uptime percentages."""
    _name = 'website.monitor.stats.mixin'
    _description = 'Website Monitor: History and Uptime'

    history_ids = fields.One2many(
        'website.monitor.history', 'server_id', string="History")
    history_count = fields.Integer(compute='_compute_history_count')
    uptime_24h = fields.Float(
        string="Uptime 24h (%)", compute='_compute_uptime')
    uptime_7d = fields.Float(string="Uptime 7d (%)", compute='_compute_uptime')
    uptime_30d = fields.Float(
        string="Uptime 30d (%)", compute='_compute_uptime')

    def _compute_history_count(self):
        """Number of history rows, for the smart button."""
        data = self.env['website.monitor.history']._read_group(
            [('server_id', 'in', self.ids)], ['server_id'], ['__count'])
        counts = {server.id: count for server, count in data}
        for monitor in self:
            monitor.history_count = counts.get(monitor.id, 0)

    def _compute_uptime(self):
        """Percentage of 'up' checks over the last 24 hours, 7 days and 30 days.

        One grouped read per period for all records; 0 when there is no data.
        """
        now = fields.Datetime.now()
        periods = (('uptime_24h', 1), ('uptime_7d', 7), ('uptime_30d', 30))
        result = {monitor.id: {} for monitor in self}
        for fname, days in periods:
            data = self.env['website.monitor.history']._read_group(
                [('server_id', 'in', self.ids),
                 ('last_checked', '>=', now - timedelta(days=days))],
                ['server_id', 'status'], ['__count'])
            totals, ups = {}, {}
            for server, status, count in data:
                totals[server.id] = totals.get(server.id, 0) + count
                if status == 'up':
                    ups[server.id] = count
            for monitor in self:
                total = totals.get(monitor.id, 0)
                result[monitor.id][fname] = (
                    100.0 * ups.get(monitor.id, 0) / total if total else 0.0)
        for monitor in self:
            for fname, _days in periods:
                monitor[fname] = result[monitor.id][fname]
