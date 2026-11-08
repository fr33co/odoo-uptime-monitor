from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

CHECKS = 'odoo.addons.server_uptime_monitor.models.monitor_checks'

OK = {'ok': True, 'http_status': 200, 'http_status_msg': "OK",
      'http_server': "nginx", 'response_time': 100.0}
FAIL = {'ok': False, 'http_status': -1, 'http_status_msg': "timeout",
        'http_server': "Not found", 'response_time': 0.0}
NO_SSL = {'ssl_status': "Not HTTPS", 'ssl_issued_by': False,
          'ssl_expiry_date': False}


def monitor_checks_values(created):
    """Agent values for a database created ``created`` with no reported expiry."""
    from odoo.addons.server_uptime_monitor.models import monitor_checks
    return monitor_checks.database_values(
        {'create_date': created.strftime('%Y-%m-%d %H:%M:%S')}, 30)


@tagged('post_install', '-at_install')
class TestWebsiteMonitor(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.group = cls.env['website.monitor.groups'].create({'name': "Ops"})
        cls.env['website.monitor.contacts'].create({
            'email': "ops@example.com", 'monitor_group_id': cls.group.id})
        cls.monitor = cls.env['website.monitor'].create({
            'name': "Site", 'url': "http://example.test",
            'monitor_group_id': cls.group.id, 'failure_threshold': 2})

    def _run(self, result):
        with patch(f'{CHECKS}.check_http', return_value=dict(result)), \
                patch(f'{CHECKS}.check_ssl', return_value=dict(NO_SSL)):
            self.monitor._apply_result(
                self.monitor._run_checks(self.monitor._check_params()))

    def _mails(self):
        return self.env['mail.mail'].search([
            ('email_to', '=', 'ops@example.com')])

    def test_alert_needs_consecutive_failures_and_is_sent_once(self):
        self._run(FAIL)
        self.assertEqual(self.monitor.consecutive_failures, 1)
        self.assertFalse(self.monitor.service_notified)
        self.assertFalse(self._mails())
        self._run(FAIL)
        self.assertTrue(self.monitor.service_notified)
        self.assertEqual(len(self._mails()), 1)
        self._run(FAIL)
        self.assertEqual(len(self._mails()), 1, "No repeated alert")

    def test_recovery_notification_and_reset(self):
        self._run(FAIL)
        self._run(FAIL)
        self._run(OK)
        self.assertEqual(self.monitor.status, 'up')
        self.assertFalse(self.monitor.service_notified)
        self.assertEqual(self.monitor.consecutive_failures, 0)
        subjects = self._mails().mapped('subject')
        self.assertTrue(any(s.startswith("Recovered") for s in subjects))

    def test_single_failure_does_not_alert(self):
        self._run(FAIL)
        self._run(OK)
        self.assertFalse(self._mails())

    def test_slow_response_is_a_problem(self):
        self.monitor.failure_threshold = 1
        self._run(dict(OK, response_time=9000.0))
        self.assertTrue(self.monitor.service_notified)

    def test_next_check_uses_interval(self):
        self.monitor.check_interval = 30
        self._run(OK)
        delta = self.monitor.next_check - self.monitor.last_checked
        self.assertEqual(delta, timedelta(minutes=30))

    def test_cron_only_checks_due_monitors(self):
        other = self.monitor.copy({'name': "Other"})
        other.next_check = fields.Datetime.now() + timedelta(hours=1)
        self.monitor.next_check = False
        with patch(f'{CHECKS}.check_http', return_value=dict(OK)), \
                patch(f'{CHECKS}.check_ssl', return_value=dict(NO_SSL)):
            self.env['website.monitor']._cron_check_websites()
        self.assertEqual(self.monitor.status, 'up')
        self.assertEqual(other.status, 'no_checked')
        self.assertEqual(self.monitor.history_count, 1)

    def test_uptime_computation(self):
        now = fields.Datetime.now()
        for status in ('up', 'up', 'up', 'down'):
            self.env['website.monitor.history'].create({
                'server_id': self.monitor.id, 'last_checked': now,
                'status': status})
        self.assertEqual(self.monitor.uptime_24h, 75.0)
        self.assertEqual(self.monitor.uptime_30d, 75.0)

    def test_expiry_alert_once_per_level(self):
        now = fields.Datetime.now()
        self.monitor.ssl_expiry_date = now + timedelta(days=10, hours=1)
        self.monitor._check_expiry_alerts()
        self.assertEqual(self.monitor.ssl_alert_level, 15)
        self.assertEqual(len(self._mails()), 1)
        self.monitor._check_expiry_alerts()
        self.assertEqual(len(self._mails()), 1)
        self.monitor.ssl_expiry_date = now + timedelta(days=5, hours=1)
        self.monitor._check_expiry_alerts()
        self.assertEqual(self.monitor.ssl_alert_level, 7)
        self.assertEqual(len(self._mails()), 2)

    def test_group_users_are_notified_without_duplicates(self):
        user = self.env['res.users'].create({
            'name': "Ops User", 'login': "ops_user",
            'email': "OPS@example.com"})
        self.group.user_ids = user
        targets = self.group._get_notification_targets()
        self.assertEqual(len(targets), 1, "Same email as the contact")
        self.assertEqual(targets[0]['partner_id'], user.partner_id.id)
        self.monitor.failure_threshold = 1
        self._run(FAIL)
        mail = self.env['mail.mail'].search([
            ('recipient_ids', 'in', user.partner_id.id)])
        self.assertEqual(len(mail), 1)

    def test_tcp_requires_port(self):
        with self.assertRaises(ValidationError):
            self.monitor.write({'check_type': 'tcp', 'tcp_port': 0})

    def test_odoo_mode_probes_login_with_a_light_get(self):
        from odoo.addons.server_uptime_monitor.models import monitor_checks
        self.assertEqual(
            monitor_checks.odoo_probe_url("https://acme.odoo.com/odoo?x=1"),
            "https://acme.odoo.com/web/login")
        self.monitor.is_odoo = True
        self.assertTrue(self.monitor._check_params()['odoo'])
        raw = type('Raw', (), {
            'read': lambda self, n, decode_content=True: b'/web/assets/x'})()
        response = type('R', (), {
            'status_code': 200, 'headers': {}, 'raw': raw, 'encoding': 'utf-8',
            'elapsed': timedelta(milliseconds=50), 'close': lambda self: None})()
        with patch(f'{CHECKS}.requests.request', return_value=response) as req:
            monitor_checks.check_http(dict(
                self.monitor._check_params(), method='HEAD'))
        args, kwargs = req.call_args
        self.assertEqual(args, ("GET", "http://example.test/web/login"))
        self.assertTrue(kwargs['stream'])

    def test_odoo_mode_rejects_a_page_that_is_not_odoo(self):
        from odoo.addons.server_uptime_monitor.models import monitor_checks
        self.monitor.is_odoo = True

        def probe(body):
            raw = type('Raw', (), {
                'read': lambda self, n, decode_content=True: body})()
            response = type('R', (), {
                'status_code': 200, 'headers': {}, 'raw': raw,
                'encoding': 'utf-8', 'elapsed': timedelta(milliseconds=50),
                'close': lambda self: None})()
            with patch(f'{CHECKS}.requests.request', return_value=response):
                return monitor_checks.check_http(self.monitor._check_params())

        self.assertTrue(probe(b'<script src="/web/assets/1/web.js">')['ok'])
        result = probe(b'<html>Welcome to nginx</html>')
        self.assertFalse(result['ok'])
        self.assertIn("does not look like Odoo", result['http_status_msg'])

    def test_agent_resources_trigger_alert(self):
        self.monitor.write({
            'is_odoo': True, 'agent_enabled': True, 'agent_token': "tok",
            'max_cpu': 80,
            'failure_threshold': 1})
        params = self.monitor._check_params()
        self.assertEqual(
            params['agent_url'], "http://example.test/server_agent/metrics")
        agent = {'cpu_percent': 95.0, 'memory_percent': 40.0,
                 'disk_percent': 50.0, 'requests_in_flight': 3,
                 'agent_status': "OK"}
        with patch(f'{CHECKS}.check_agent', return_value=agent):
            self._run(OK)
        self.assertEqual(self.monitor.cpu_percent, 95.0)
        self.assertTrue(self.monitor.service_notified)
        self.assertIn("CPU", self.monitor._resource_problems()[0])

    def test_unreachable_agent_is_not_an_incident(self):
        self.monitor.write({
            'is_odoo': True, 'agent_enabled': True, 'failure_threshold': 1})
        agent = {'cpu_percent': 0.0, 'memory_percent': 0.0,
                 'disk_percent': 0.0, 'requests_in_flight': 0,
                 'agent_status': "Error: refused"}
        with patch(f'{CHECKS}.check_agent', return_value=agent):
            self._run(OK)
        self.assertFalse(self.monitor.service_notified)

    def test_resources_ignored_when_not_odoo(self):
        self.monitor.write({'agent_enabled': True, 'is_odoo': False})
        self.assertFalse(self.monitor._check_params()['agent'])

    def test_check_registry_is_extensible_without_touching_the_model(self):
        from odoo.addons.server_uptime_monitor.models import monitor_checks
        calls = []

        @monitor_checks.register(lambda p: p.get('custom'))
        def custom_check(p):
            calls.append(p['url'])
            return {'http_server': "custom"}

        try:
            with patch(f'{CHECKS}.check_http', return_value=dict(OK)), \
                    patch(f'{CHECKS}.check_ssl', return_value=dict(NO_SSL)):
                vals = monitor_checks.run_all({
                    'check_type': 'http', 'url': "x", 'custom': True})
                skipped = monitor_checks.run_all({
                    'check_type': 'http', 'url': "x"})
        finally:
            monitor_checks.CHECKS.pop()
        self.assertEqual(vals['http_server'], "custom")
        self.assertEqual(skipped['http_server'], "nginx")
        self.assertEqual(calls, ["x"])

    def test_database_validity_alerts_once_per_level_and_when_expired(self):
        self.monitor.write({
            'is_odoo': True, 'agent_enabled': True, 'agent_token': "tok",
            'db_alert_days': "30,15,7,5,1", 'failure_threshold': 99})
        now = fields.Datetime.now()
        ok_agent = {'cpu_percent': 1.0, 'memory_percent': 1.0,
                    'disk_percent': 1.0, 'requests_in_flight': 0,
                    'agent_status': "OK", 'db_expiry_reason': "trial"}

        def run(days):
            agent = dict(ok_agent, db_expiry_date=now + timedelta(
                days=days, hours=1))
            with patch(f'{CHECKS}.check_agent', return_value=agent):
                self._run(OK)

        run(20)
        self.assertEqual(self.monitor.db_alert_level, 30)
        self.assertEqual(len(self._mails()), 1)
        run(20)
        self.assertEqual(len(self._mails()), 1, "same level: no new mail")
        run(6)
        self.assertEqual(self.monitor.db_alert_level, 7)
        run(4)
        self.assertEqual(self.monitor.db_alert_level, 5, "5 days before")
        self.assertEqual(len(self._mails()), 3)
        run(-2)
        self.assertEqual(self.monitor.db_alert_level, 1, "already expired")
        self.assertEqual(len(self._mails()), 4)
        self.assertLess(self.monitor.db_days_left, 0)
        self.assertEqual(self.monitor.color, 3, "yellow while expiring")

    def test_database_warning_defaults_to_five_days(self):
        self.monitor.write({
            'is_odoo': True, 'agent_enabled': True, 'agent_token': "tok",
            'failure_threshold': 99})
        self.assertEqual(self.monitor.db_alert_days, "5")
        now = fields.Datetime.now()
        agent = {'cpu_percent': 1.0, 'memory_percent': 1.0,
                 'disk_percent': 1.0, 'requests_in_flight': 0,
                 'agent_status': "OK"}

        def run(days):
            with patch(f'{CHECKS}.check_agent', return_value=dict(
                    agent, db_expiry_date=now + timedelta(
                        days=days, hours=1))):
                self._run(OK)

        run(20)
        run(6)
        self.assertEqual(len(self._mails()), 0, "nothing before 5 days")
        run(4)
        self.assertEqual(len(self._mails()), 1, "the 5-day warning")
        run(2)
        self.assertEqual(len(self._mails()), 1, "only once")

    def test_warning_days_are_configurable(self):
        self.monitor.write({
            'is_odoo': True, 'agent_enabled': True, 'agent_token': "tok",
            'db_alert_days': "10, 3", 'failure_threshold': 99})
        self.assertEqual(
            [s[3] for s in self.monitor._expiry_sources() if s[2] == 'db_expiry'],
            [(3, 10)])
        now = fields.Datetime.now()
        ok_agent = {'cpu_percent': 1.0, 'memory_percent': 1.0,
                    'disk_percent': 1.0, 'requests_in_flight': 0,
                    'agent_status': "OK"}

        def run(days):
            agent = dict(ok_agent, db_expiry_date=now + timedelta(
                days=days, hours=1))
            with patch(f'{CHECKS}.check_agent', return_value=agent):
                self._run(OK)

        run(20)
        self.assertEqual(len(self._mails()), 0, "outside every configured level")
        run(9)
        self.assertEqual(self.monitor.db_alert_level, 10)
        run(2)
        self.assertEqual(self.monitor.db_alert_level, 3)
        self.assertEqual(len(self._mails()), 2)

    def test_warning_days_must_be_whole_numbers(self):
        for field in ('expiry_alert_days', 'db_alert_days'):
            for bad in ("abc", "7;3", "-5,7", "1.5"):
                with self.subTest(field=field, value=bad), \
                        self.assertRaises(ValidationError):
                    self.monitor.write({field: bad})
        self.monitor.write({'expiry_alert_days': "14,2"})
        self.assertEqual(self.monitor._expiry_sources()[0][3], (2, 14))

    def test_five_day_level_is_for_the_database_only(self):
        self.monitor.write({'is_odoo': True, 'agent_enabled': True})
        levels = {s[2]: s[3] for s in self.monitor._expiry_sources()}
        self.assertIn(5, levels['db_expiry'])
        self.assertNotIn(5, levels['ssl_expiry'])
        self.assertNotIn(5, levels['domain_expiry'])

    def test_database_validity_ignored_when_not_odoo(self):
        self.monitor.write({
            'agent_enabled': True, 'is_odoo': False,
            'db_expiry_date': fields.Datetime.now() + timedelta(days=1)})
        self.assertEqual(
            [s[2] for s in self.monitor._expiry_sources()],
            ['ssl_expiry', 'domain_expiry'])
        self.assertFalse(self.monitor._db_expiring())

    def test_agent_check_reads_the_database_validity(self):
        from odoo.addons.server_uptime_monitor.models import monitor_checks
        payload = {
            'cpu': {'percent': 5.0}, 'memory': {'percent': 6.0},
            'disks': [{'percent': 7.0}], 'requests': {'peak': 1},
            'database': {'expiration_date': "2027-01-31 12:30:00",
                         'expiration_reason': "trial"}}
        response = type('R', (), {
            'status_code': 200, 'json': lambda self: payload})()
        params = {'agent_url': "http://x", 'agent_token': "t", 'timeout': 5,
                  'verify_ssl': True}
        with patch(f'{CHECKS}.requests.get', return_value=response):
            vals = monitor_checks.check_agent(params)
            payload.pop('database')
            old_agent = monitor_checks.check_agent(params)
        self.assertEqual(vals['agent_status'], "OK")
        self.assertEqual(vals['db_expiry_date'].year, 2027)
        self.assertEqual(vals['db_expiry_reason'], "trial")
        self.assertFalse(old_agent['db_expiry_date'], "agent without the field")
        self.assertEqual(monitor_checks.parse_odoo_datetime("2027-02-01"),
                         monitor_checks.parse_odoo_datetime("2027-02-01 00:00:00"))
        self.assertFalse(monitor_checks.parse_odoo_datetime("garbage"))

    def test_database_expiry_estimate_only_when_none_is_reported(self):
        from odoo.addons.server_uptime_monitor.models import monitor_checks
        values = monitor_checks.database_values
        created = "2026-11-04 15:26:07"
        # reported date wins over any estimate
        real = values({'expiration_date': "2027-03-01 00:00:00",
                       'create_date': created}, 30)
        self.assertEqual(real['db_expiry_date'].month, 3)
        self.assertFalse(real['db_expiry_estimated'])
        # none reported: creation date + 30 days, flagged as estimate
        estimate = values({'create_date': created}, 30)
        self.assertEqual(estimate['db_expiry_date'].strftime('%Y-%m-%d'),
                         "2026-12-04")
        self.assertTrue(estimate['db_expiry_estimated'])
        # estimate disabled, or no creation date: nothing
        self.assertFalse(values({'create_date': created}, 0)['db_expiry_date'])
        self.assertFalse(values({}, 30)['db_expiry_date'])

    def test_estimated_expiry_raises_the_same_alerts(self):
        self.monitor.write({
            'is_odoo': True, 'agent_enabled': True, 'agent_token': "tok",
            'db_alert_days': "30,15,7,5,1", 'failure_threshold': 99})
        created = fields.Datetime.now() - timedelta(days=20)
        agent = dict(
            {'cpu_percent': 1.0, 'memory_percent': 1.0, 'disk_percent': 1.0,
             'requests_in_flight': 0, 'agent_status': "OK"},
            **monitor_checks_values(created))
        with patch(f'{CHECKS}.check_agent', return_value=agent):
            self._run(OK)
        self.assertTrue(self.monitor.db_expiry_estimated)
        self.assertEqual(self.monitor.db_alert_level, 15)
        self.assertEqual(len(self._mails()), 1)


@tagged('post_install', '-at_install')
class TestSecurity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env['website.monitor.groups'].create({'name': "Ops"})
        cls.env['website.monitor.contacts'].create({
            'email': "ops@example.com", 'monitor_group_id': group.id})
        cls.monitor = cls.env['website.monitor'].create({
            'name': "Site", 'url': "https://example.test",
            'monitor_group_id': group.id, 'custom_headers': "X-Key: secret",
            'auth_user': "bob", 'auth_password': "pw"})
        cls.reader = new_test_user(
            cls.env, 'monitor_reader',
            groups='server_uptime_monitor.group_website_monitor_user')

    def test_reader_cannot_read_secrets(self):
        monitor = self.monitor.with_user(self.reader)
        self.assertEqual(monitor.name, "Site")
        for field in ('custom_headers', 'auth_user', 'auth_password', 'agent_token'):
            with self.assertRaises(AccessError, msg=field):
                monitor.read([field])

    def test_checks_still_use_the_secrets(self):
        params = self.monitor.with_user(self.reader)._check_params()
        self.assertEqual(params['headers'], {'X-Key': "secret"})
        self.assertEqual(params['auth'], ("bob", "pw"))

    def test_reader_cannot_trigger_alert_emails(self):
        with self.assertRaises(AccessError):
            self.monitor.with_user(self.reader).action_send_mail()
        self.assertFalse(self.env['mail.mail'].search([
            ('email_to', '=', 'ops@example.com')]))
        self.monitor.action_send_mail()  # a manager (here, superuser) can
        self.assertTrue(self.env['mail.mail'].search([
            ('email_to', '=', 'ops@example.com')]))

    def test_cron_entry_point_is_private(self):
        monitors = self.env['website.monitor']
        self.assertFalse(hasattr(monitors, 'check_website_monitor_cron'))
        self.assertTrue(callable(monitors._cron_check_websites))

    def test_agent_url_must_be_https(self):
        with self.assertRaises(ValidationError):
            self.monitor.agent_url = "http://agent.example.test/server_agent/metrics"
        self.monitor.agent_url = "https://agent.example.test/server_agent/metrics"
        self.monitor.agent_url = "http://localhost:8069/server_agent/metrics"
