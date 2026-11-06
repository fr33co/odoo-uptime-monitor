from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

DEFAULT_EXPIRY_DAYS = "30,15,7,1"


def parse_days(text, default=DEFAULT_EXPIRY_DAYS):
    """Parse ``"30, 15,7"`` into ascending whole days ``(7, 15, 30)``.

    Raises ``ValueError`` on anything that is not a comma-separated list of
    non-negative integers. An empty text falls back to ``default``.
    """
    tokens = [t.strip() for t in (text or default).split(',') if t.strip()]
    days = {int(t) for t in tokens}
    if not days or min(days) < 0 or not all(t.isdigit() for t in tokens):
        raise ValueError(text)
    return tuple(sorted(days))


class WebsiteMonitorIncident(models.AbstractModel):
    """Incident state machine: apply a check result, alert once, notify recovery.

    The host model must provide the result fields (``website.monitor.result.mixin``),
    ``check_interval``, ``failure_threshold``, ``max_response_time`` and
    ``_extra_problems()``, a hook returning additional problems (e.g. resources).
    """
    _name = 'website.monitor.incident.mixin'
    _inherit = ['website.monitor.notification.mixin']
    _description = 'Website Monitor: Incident Tracking'

    expiry_alert_days = fields.Char(
        string="Warn SSL / Domain Expiry (days)", default=DEFAULT_EXPIRY_DAYS,
        help="Days before the SSL certificate or the domain expires at which "
             "the group is warned, comma-separated (e.g. 30,15,7,1). One "
             "warning per level.")
    consecutive_failures = fields.Integer(readonly=True)
    problem_since = fields.Datetime(readonly=True)
    last_downtime_minutes = fields.Integer(
        string="Last Downtime (min)", readonly=True)
    service_notified = fields.Boolean(string="Alert Sent", readonly=True)
    ssl_alert_level = fields.Integer(readonly=True)
    domain_alert_level = fields.Integer(readonly=True)

    @api.constrains('expiry_alert_days')
    def _check_expiry_alert_days(self):
        """The warning days must be a comma-separated list of whole numbers."""
        for monitor in self:
            try:
                parse_days(monitor.expiry_alert_days)
            except ValueError:
                raise ValidationError(self.env._(
                    "Warning days must be whole numbers separated by commas, "
                    "e.g. 30,15,7,1."))

    def _apply_result(self, result):
        """Store a check result and update the incident state."""
        self.ensure_one()
        now = fields.Datetime.now()
        result = dict(result)
        ok = result.pop('ok')
        vals = dict(result, last_checked=now)
        vals['status'] = 'up' if ok else 'down'
        vals['next_check'] = now + timedelta(minutes=self.check_interval)
        self.write(vals)
        if self._has_problem():
            self._open_or_continue_incident(now)
        else:
            self._close_incident(now)
        self._check_expiry_alerts()

    def _open_or_continue_incident(self, now):
        """Count the failure and alert once the threshold is reached."""
        failures = self.consecutive_failures + 1
        self.write({
            'consecutive_failures': failures,
            'problem_since': self.problem_since or now,
        })
        if failures >= self.failure_threshold and not self.service_notified:
            self._notify('alert')
            self.service_notified = True

    def _close_incident(self, now):
        """Send the recovery notice if an alert was sent, then reset the counters."""
        if self.service_notified:
            minutes = int((now - self.problem_since).total_seconds() // 60)
            self.last_downtime_minutes = minutes
            self._notify('recovery')
        self.write({
            'consecutive_failures': 0,
            'problem_since': False,
            'service_notified': False,
        })

    def _has_problem(self):
        """Whether the last result needs attention: down, SSL error, slow or an extra problem.

        An SSL certificate that is merely close to expiry is not a problem here;
        it is handled by :meth:`_check_expiry_alerts`.
        """
        self.ensure_one()
        ssl_ok = (not self.ssl_status
                  or self.ssl_status in ("Valid", "Expiring soon", "Not HTTPS"))
        return bool(
            self.status == 'down'
            or not ssl_ok
            or (self.max_response_time
                and self.response_time > self.max_response_time)
            or self._extra_problems()
        )

    def _expiry_sources(self):
        """``(date field, alert level field, notification kind, days levels)`` of everything that expires.

        Extended by the host model (e.g. the database validity).
        """
        levels = parse_days(self.expiry_alert_days)
        return [
            ('ssl_expiry_date', 'ssl_alert_level', 'ssl_expiry', levels),
            ('domain_expiry_date', 'domain_alert_level', 'domain_expiry', levels),
        ]

    def _check_expiry_alerts(self):
        """Alert once each time an expiry crosses one of its day levels (or has passed)."""
        self.ensure_one()
        for date_fname, level_fname, kind, levels in self._expiry_sources():
            expiry = self[date_fname]
            if not expiry:
                continue
            days = (expiry - fields.Datetime.now()).days
            level = next((lvl for lvl in levels if days <= lvl), 0)
            previous = self[level_fname]
            if level and (not previous or level < previous):
                self._notify(kind)
            if level != previous:
                self[level_fname] = level
