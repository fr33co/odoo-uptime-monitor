import os
import tempfile
import time
from collections import namedtuple
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase

from ..controllers.main import _ip_allowed
from ..models import inflight, metrics

URL = '/server_agent/metrics'
Usage = namedtuple('Usage', 'total used free percent')


class IsolatedState(TransactionCase):
    """Run against a private temp directory so the live server's files are untouched."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        directory = self.tmp.name
        for target, attr, value in (
                (inflight, 'DIR', directory),
                (inflight, 'READ_MARK', os.path.join(directory, 'last_read')),
                (metrics, 'CPU_SAMPLE', os.path.join(directory, 'cpu_sample'))):
            patcher = patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(inflight._state, {
            'current': 0, 'peak': 0, 'peak_started': 0.0})
        patcher.start()
        self.addCleanup(patcher.stop)


@tagged('post_install', '-at_install')
class TestInflight(IsolatedState):

    def test_counts_requests_in_flight(self):
        inflight.enter()
        inflight.enter()
        inflight.leave()
        self.assertEqual(inflight.read()[0], 1)
        inflight.leave()
        self.assertEqual(inflight.read()[0], 0)
        inflight.leave()  # never goes negative
        self.assertEqual(inflight.read()[0], 0)

    def test_peak_survives_until_read_then_resets(self):
        inflight.enter()
        inflight.enter()
        inflight.leave()
        inflight.leave()
        self.assertEqual(inflight.read(), (0, 2), "peak seen between polls")
        self.assertEqual(inflight.read(), (0, 0), "already reported")
        inflight.enter()
        self.assertEqual(inflight.read(), (1, 1))

    def test_files_of_dead_processes_are_ignored_and_removed(self):
        dead = os.path.join(inflight.DIR, '999999999')
        with open(dead, 'w') as f:
            f.write('5 5 %f' % time.time())
        with patch.object(inflight.psutil, 'pid_exists', return_value=False):
            self.assertEqual(inflight.read(), (0, 0))
        self.assertFalse(os.path.exists(dead))

    def test_sums_live_processes(self):
        with open(os.path.join(inflight.DIR, '4242'), 'w') as f:
            f.write('1 3 %f' % (time.time() + 10))
        inflight.enter()
        with patch.object(inflight.psutil, 'pid_exists', return_value=True):
            current, peak = inflight.read()
        self.assertEqual(current, 2)
        self.assertEqual(peak, 4)


@tagged('post_install', '-at_install')
class TestUntrustedDirectory(IsolatedState):

    def test_directory_open_to_others_is_ignored(self):
        os.chmod(inflight.DIR, 0o777)
        inflight.enter()
        self.assertEqual(os.listdir(inflight.DIR), [], "nothing written")
        self.assertEqual(inflight.read(), (0, 0))

    def test_symlink_in_the_directory_is_not_followed(self):
        victim = os.path.join(self.tmp.name, 'victim')
        with open(victim, 'w') as f:
            f.write('keep')
        os.symlink(victim, os.path.join(inflight.DIR, str(os.getpid())))
        inflight.enter()
        with open(victim) as f:
            self.assertEqual(f.read(), 'keep')


@tagged('post_install', '-at_install')
class TestMetrics(IsolatedState):

    def test_cpu_uses_delta_from_the_shared_sample(self):
        with open(metrics.CPU_SAMPLE, 'w') as f:
            f.write('%f 100.0 200.0' % time.time())
        with patch.object(metrics, '_busy_total', return_value=(150.0, 300.0)):
            percent = metrics.cpu_percent()[0]
        self.assertAlmostEqual(percent, 50.0)

    def test_cpu_falls_back_to_a_short_sample_without_baseline(self):
        with patch.object(metrics.psutil, 'cpu_percent', return_value=12.3):
            self.assertEqual(metrics.cpu_percent()[0], 12.3)

    def test_cpu_ignores_an_old_baseline(self):
        with open(metrics.CPU_SAMPLE, 'w') as f:
            f.write('%f 0.0 0.0' % (time.time() - 2 * metrics.MAX_SAMPLE_AGE))
        with patch.object(metrics.psutil, 'cpu_percent', return_value=7.0):
            self.assertEqual(metrics.cpu_percent()[0], 7.0)

    def test_same_disk_is_listed_once(self):
        usage = Usage(1000, 400, 600, 40.0)
        with patch.object(metrics.psutil, 'disk_usage', return_value=usage), \
                patch.dict(metrics.config.options, {'data_dir': '/data'}):
            result = metrics.disks()
        self.assertEqual([d['name'] for d in result], ['data_dir'])

    def test_database_validity_is_reported(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_str('database.expiration_date', "2027-01-31 12:30:00")
        params.set_str('database.expiration_reason', "trial")
        params.set_str('database.create_date', "2026-11-04 15:26:07")
        self.env.flush_all()  # metrics read with raw SQL
        self.assertEqual(
            metrics.database_validity(self.env.cr),
            {'expiration_date': "2027-01-31 12:30:00",
             'expiration_reason': "trial",
             'create_date': "2026-11-04 15:26:07"})
        self.env.cr.execute(
            "DELETE FROM ir_config_parameter WHERE key LIKE 'database.expiration%'")
        self.assertEqual(
            metrics.database_validity(self.env.cr),
            {'expiration_date': None, 'expiration_reason': None,
             'create_date': "2026-11-04 15:26:07"})

    def test_collect_shape(self):
        data = metrics.collect(self.env.cr)
        self.assertEqual(
            set(data),
            {'timestamp', 'cpu', 'memory', 'disks', 'database', 'requests'})
        self.assertTrue(0 <= data['cpu']['percent'] <= 100)
        self.assertTrue(0 <= data['memory']['percent'] <= 100)
        self.assertTrue(data['disks'])
        self.assertEqual(
            set(data['requests']),
            {'in_flight', 'peak', 'workers', 'db_active_queries'})


@tagged('post_install', '-at_install')
class TestAccess(TransactionCase):

    def test_ip_allowlist(self):
        self.assertTrue(_ip_allowed('', '1.2.3.4'), "empty list allows all")
        self.assertTrue(_ip_allowed(False, '1.2.3.4'))
        self.assertTrue(_ip_allowed('10.0.0.0/8, 5.5.5.5', '10.1.2.3'))
        self.assertTrue(_ip_allowed('10.0.0.0/8, 5.5.5.5', '5.5.5.5'))
        self.assertFalse(_ip_allowed('5.5.5.5', '6.6.6.6'))
        self.assertFalse(_ip_allowed('5.5.5.5', 'not-an-ip'))

    def test_token_is_generated_on_install(self):
        token = self.env['ir.config_parameter'].sudo().get_str(
            'server_resource_agent.token')
        self.assertGreaterEqual(len(token), 32)


@tagged('post_install', '-at_install')
class TestEndpoint(HttpCase, IsolatedState):

    def setUp(self):
        super().setUp()
        self.params = self.env['ir.config_parameter'].sudo()
        self.params.set_str('server_resource_agent.token', 'secret-token')
        self.params.set_str('server_resource_agent.allowed_ips', '')

    def _get(self, token=None):
        headers = {'Authorization': 'Bearer %s' % token} if token else {}
        return self.url_open(URL, headers=headers)

    def test_returns_metrics_with_the_right_token(self):
        response = self._get('secret-token')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn('cpu', response.json())

    def test_accepts_the_x_agent_token_header(self):
        response = self.url_open(URL, headers={'X-Agent-Token': 'secret-token'})
        self.assertEqual(response.status_code, 200)

    def test_hides_the_endpoint_without_a_valid_token(self):
        self.assertEqual(self._get().status_code, 404)
        self.assertEqual(self._get('wrong').status_code, 404)

    def test_no_configured_token_means_closed(self):
        self.params.set_str('server_resource_agent.token', '')
        self.assertEqual(self._get('anything').status_code, 404)

    def test_ip_allowlist_applies_before_the_token(self):
        self.params.set_str('server_resource_agent.allowed_ips', '10.0.0.0/8')
        self.assertEqual(self._get('secret-token').status_code, 404)
        self.params.set_str(
            'server_resource_agent.allowed_ips', '127.0.0.1, ::1')
        self.assertEqual(self._get('secret-token').status_code, 200)

    def test_other_requests_are_counted_but_the_probe_is_not(self):
        inflight.read()  # reset the peaks
        self._get('secret-token')
        self.assertEqual(inflight.read()[1], 0, "the agent route is not counted")
        self.url_open('/web/login')
        self.assertGreaterEqual(inflight.read()[1], 1)
