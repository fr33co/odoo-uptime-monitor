"""Network checks, free of ORM access so they can run in worker threads."""
import re
import socket
import ssl
from datetime import datetime, timedelta, timezone
from http.client import responses
from urllib.parse import urlparse

import requests

RDAP_URL = 'https://rdap.org/domain/%s'


def _utcnow():
    """Return the current UTC time as a naive datetime (Odoo's datetime convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hostname_of(url):
    """Split ``url`` into ``(hostname, port, scheme)``; a bare host is read as https."""
    parsed = urlparse(url if '://' in url else 'https://' + url)
    return parsed.hostname, parsed.port, parsed.scheme


def parse_headers(text):
    """Parse one ``Name: value`` per line text into a headers dict, skipping malformed lines."""
    headers = {}
    for line in (text or '').splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            if key.strip():
                headers[key.strip()] = value.strip()
    return headers


ODOO_PROBE_PATH = '/web/login'
ODOO_MAX_BODY = 64 * 1024
# Strings present in the Odoo login page (any recent version).
ODOO_MARKERS = re.compile(
    r'/web/assets/|__session_info__|oe_login_form|o_database_list|odoo',
    re.IGNORECASE)
ODOO_USER_AGENT = 'OdooUptimeMonitor/1.0 (light probe)'


def odoo_probe_url(url):
    """Return the light Odoo probe URL (``/web/login``) of the origin of ``url``.

    The login page is rendered by a worker and needs the database, so its
    latency reflects a loaded instance, yet it is one cheap anonymous GET.
    """
    parsed = urlparse(url if '://' in url else 'https://' + url)
    return '%s://%s%s' % (parsed.scheme, parsed.netloc, ODOO_PROBE_PATH)


def read_head(response, limit=ODOO_MAX_BODY):
    """Read at most ``limit`` bytes of a streamed response as text ('' on error)."""
    try:
        return response.raw.read(limit, decode_content=True).decode(
            response.encoding or 'utf-8', errors='replace')
    except (OSError, ValueError, requests.RequestException):
        return ''


def looks_like_odoo(text):
    """Whether ``text`` contains markers of an Odoo page."""
    return bool(ODOO_MARKERS.search(text))


def check_http(p):
    """Return the field values describing an HTTP check.

    ``p`` keys: url, method, timeout, verify_ssl, headers, auth,
    expected_status, keyword, odoo.

    In Odoo mode a single anonymous GET hits ``/web/login``; the time to the
    response headers is measured, then at most 64 KiB of the page are read to
    confirm it really is Odoo (so a 404 or a foreign site is not reported as
    a healthy instance). The probe stays light on the instance.
    """
    vals = {'ok': False}
    url, method, headers = p['url'], p['method'], p['headers']
    stream = False
    if p.get('odoo'):
        url, method, stream = odoo_probe_url(url), 'GET', True
        headers = dict({'User-Agent': ODOO_USER_AGENT}, **headers)
    try:
        response = requests.request(
            method, url, timeout=p['timeout'], stream=stream,
            verify=p['verify_ssl'], headers=headers, auth=p['auth'])
    except requests.RequestException as e:
        vals.update(
            http_server="Not found", http_status=-1,
            http_status_msg=str(e), response_time=0.0)
        return vals
    code = response.status_code
    expected = p['expected_status']
    vals.update(
        http_server=response.headers.get('Server', "Unknown"),
        http_status=code,
        http_status_msg=responses.get(code, ""),
        response_time=response.elapsed.total_seconds() * 1000,
    )
    ok = (code == expected) if expected else code < 500
    if p.get('odoo'):
        text = read_head(response)
        if ok and not looks_like_odoo(text):
            ok = False
            vals['http_status_msg'] = "Response does not look like Odoo"
    else:
        text = response.text if p['keyword'] else None
    if ok and p['keyword'] and p['keyword'] not in text:
        ok = False
        vals['http_status_msg'] = "Keyword not found: %s" % p['keyword']
    vals['ok'] = ok
    response.close()
    return vals


def check_tcp(p):
    """Return the values of a TCP connect check on host:port."""
    host, _port, _scheme = hostname_of(p['url'])
    vals = {'ok': False, 'http_server': "TCP", 'http_status': -1}
    start = _utcnow()
    try:
        with socket.create_connection((host, p['port']), timeout=p['timeout']):
            pass
    except OSError as e:
        vals.update(http_status_msg=str(e), response_time=0.0)
        return vals
    vals.update(
        ok=True, http_status=0, http_status_msg="Connected",
        response_time=(_utcnow() - start).total_seconds() * 1000)
    return vals


def check_ssl(p):
    """Return SSL values: ssl_status, ssl_issued_by, ssl_expiry_date."""
    vals = {'ssl_issued_by': False, 'ssl_expiry_date': False}
    host, port, scheme = hostname_of(p['url'])
    if scheme != 'https' or not host:
        vals['ssl_status'] = "Not HTTPS"
        return vals
    if not p['verify_ssl']:
        vals['ssl_status'] = "Not HTTPS"
        return vals
    try:
        context = ssl.create_default_context()
        with socket.create_connection(
                (host, port or 443), timeout=p['timeout']) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        issuer = dict(x[0] for x in cert['issuer'])
        expiry = datetime.fromtimestamp(
            ssl.cert_time_to_seconds(cert['notAfter']),
            timezone.utc).replace(tzinfo=None)
        vals['ssl_issued_by'] = issuer.get(
            'organizationName', issuer.get('commonName', "Unknown"))
        vals['ssl_expiry_date'] = expiry
        vals['ssl_status'] = (
            "Valid" if (expiry - _utcnow()).days > 30 else "Expiring soon")
    except ssl.SSLError as e:
        vals['ssl_status'] = "SSL Error: %s" % e
    except socket.gaierror as e:
        vals['ssl_status'] = "Resolution Error: %s" % e
    except (OSError, KeyError, ValueError) as e:
        vals['ssl_status'] = "Error: %s" % e
    return vals


# Second-level labels used under country codes (com.co, co.uk, gob.mx, ...).
SECOND_LEVEL = {'com', 'net', 'org', 'gov', 'gob', 'edu', 'co', 'ac', 'mil'}
# Domains owned by the hosting platform, not by the customer.
PLATFORM_DOMAINS = {'odoo.com', 'odoo.sh', 'odoo.dev'}


def registered_domain(host):
    """Registrable domain of ``host``: last two labels, three under ``com.co``-style suffixes."""
    if not host or re.fullmatch(r'[\d.:]+', host):
        return None
    labels = host.lower().split('.')
    if len(labels) > 2 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL:
        return '.'.join(labels[-3:])
    return '.'.join(labels[-2:])


def check_domain(p):
    """Return the domain expiry values (RDAP), ``domain_status`` and ``domain_checked_at``."""
    return dict(_check_domain(p), domain_checked_at=_utcnow())


def _check_domain(p):
    """RDAP lookup behind :func:`check_domain`."""
    host, _port, _scheme = hostname_of(p['url'])
    domain = registered_domain(host)
    if not domain:
        return {'domain_expiry_date': False, 'domain_status': "Not applicable (IP address)"}
    if domain in PLATFORM_DOMAINS:
        return {'domain_expiry_date': False,
                'domain_status': "Not applicable (%s is managed by the platform)" % domain}
    try:
        data = requests.get(RDAP_URL % domain, timeout=max(p['timeout'], 15)).json()
    except (requests.RequestException, ValueError) as e:
        return {'domain_status': "Lookup failed for %s: %s" % (domain, e)}
    for event in data.get('events', []):
        if event.get('eventAction') == 'expiration':
            try:
                expiry = datetime.fromisoformat(
                    event['eventDate'].replace('Z', '+00:00'))
            except (KeyError, ValueError):
                break
            return {'domain_expiry_date': expiry.astimezone(
                timezone.utc).replace(tzinfo=None),
                'domain_status': "OK (%s)" % domain}
    return {'domain_status': "No expiration date published for %s" % domain}


AGENT_PATH = '/server_agent/metrics'


def agent_url(url):
    """Default agent URL: the origin of ``url`` plus the agent path."""
    parsed = urlparse(url if '://' in url else 'https://' + url)
    return '%s://%s%s' % (parsed.scheme, parsed.netloc, AGENT_PATH)


def parse_odoo_datetime(text):
    """Parse Odoo's ``YYYY-MM-DD[ HH:MM:SS]`` (UTC) into a naive datetime, or ``False``."""
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
    return False


def database_values(database, estimate_days=0):
    """Database validity fields from the agent's ``database`` section.

    The reported expiration date wins. Without one, and if ``estimate_days`` is
    set, the expiry is estimated as the creation date plus that many days and
    flagged as an estimate.
    """
    expiry = parse_odoo_datetime(database.get('expiration_date'))
    created = parse_odoo_datetime(database.get('create_date'))
    estimated = False
    if not expiry and estimate_days and created:
        expiry, estimated = created + timedelta(days=estimate_days), True
    return {
        'db_expiry_date': expiry,
        'db_expiry_estimated': estimated,
        'db_expiry_reason': database.get('expiration_reason') or False,
        'db_create_date': created,
    }


def check_agent(p):
    """Return the resource figures published by the Server Resource Agent.

    ``p`` keys: agent_url, agent_token, timeout, verify_ssl. A failure only
    sets ``agent_status``: an unreachable agent does not mean the site is down,
    and it leaves the last known database validity untouched.
    """
    empty = {'cpu_percent': 0.0, 'memory_percent': 0.0, 'disk_percent': 0.0,
             'requests_in_flight': 0}
    try:
        response = requests.get(
            p['agent_url'], timeout=p['timeout'], verify=p['verify_ssl'],
            headers={'Authorization': 'Bearer %s' % p['agent_token'],
                     'User-Agent': ODOO_USER_AGENT})
        if response.status_code != 200:
            return dict(empty, agent_status="HTTP %s (check URL and token)"
                        % response.status_code)
        data = response.json()
        disks = data.get('disks') or []
        return {
            **database_values(data.get('database') or {}, p.get('db_estimate_days')),
            'cpu_percent': float(data['cpu']['percent']),
            'memory_percent': float(data['memory']['percent']),
            'disk_percent': max((d['percent'] for d in disks), default=0.0),
            'requests_in_flight': int(data['requests']['peak']),
            'agent_status': "OK",
        }
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        return dict(empty, agent_status="Error: %s" % e)


# ----------------------------------------------------------------------
# Check registry
# ----------------------------------------------------------------------
# Each registered check says when it applies and returns the field values it
# produces. :func:`run_all` merges them in registration order, so a new kind of
# check is added here without touching the model. The wrappers look the
# functions up by name at call time (tests patch them on this module).

CHECKS = []


def register(applies):
    """Decorator: register ``func(p) -> dict`` to run when ``applies(p)`` is true."""
    def decorator(func):
        CHECKS.append((applies, func))
        return func
    return decorator


def run_all(p):
    """Run every applicable registered check and merge their values."""
    vals = {}
    for applies, func in CHECKS:
        if applies(p):
            vals.update(func(p))
    return vals


@register(lambda p: p['check_type'] == 'http')
def _run_http(p):
    return check_http(p)


@register(lambda p: p['check_type'] == 'tcp')
def _run_tcp(p):
    return dict(check_tcp(p), ssl_status=False, ssl_issued_by=False,
                ssl_expiry_date=False)


@register(lambda p: p['check_type'] == 'http')
def _run_ssl(p):
    return check_ssl(p)


@register(lambda p: p.get('agent'))
def _run_agent(p):
    return check_agent(p)


@register(lambda p: p.get('check_domain'))
def _run_domain(p):
    return check_domain(p)
