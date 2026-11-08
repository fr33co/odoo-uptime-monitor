# Technical documentation

Code examples correspond to the `20.0` branch; differences in the other branches are in §9.

## 1. Structure

```
server_uptime_monitor/
├── controllers/main.py           # /status (public page)
├── data/ir_cron.xml              # cron every minute
├── data/mail_template.xml        # alert, recovery, expiry
├── models/
│   ├── monitor_checks.py         # network checks, NO ORM (thread-safe) + registry
│   ├── website_monitor.py        # main model: configuration and orchestration
│   ├── monitor_result.py         # mixin: last-result fields
│   ├── monitor_incident.py       # mixin: state machine, alerts, expiries
│   ├── monitor_notification.py   # mixin: email sending
│   ├── monitor_resources.py      # mixin: agent, thresholds, DB validity
│   ├── monitor_stats.py          # mixin: history and uptime
│   ├── website_monitor_history.py / _groups.py / _contacts.py
├── security/                     # groups and access rights
├── views/                        # kanban, list, form, charts, status
└── tests/
server_resource_agent/
├── controllers/main.py           # GET /server_agent/metrics
├── models/ir_http.py             # counts in-flight requests (_dispatch)
├── models/inflight.py            # cross-process counter (one file per pid)
├── models/metrics.py             # CPU, memory, disks, DB validity
└── tests/
```

Dependencies: `server_uptime_monitor` → `mail` and `requests`; `server_resource_agent` → `base` and `psutil`.

## 2. Flow of a check

```
cron (1 min) _cron_check_websites
 ├─ search monitors with empty or due next_check
 ├─ main thread: m._check_params()  → flat dict (reads ORM/sudo here)
 ├─ ThreadPoolExecutor(max 10): checks.run_all(params)   ← network only, no ORM
 └─ main thread, per monitor:
      _apply_result(result)  → writes, incident, expiry alerts
      _prepare_history_vals()
    create(history_vals) in batch
```

Design rule: **threads never touch the ORM**. Everything they need (including the password, via `sudo`) is copied into a `dict` beforehand. A failure on one monitor is logged (`_logger.exception`) and does not stop the others.

## 3. Check registry (`monitor_checks.py`)

```python
@register(lambda p: p.get('agent'))
def _run_agent(p):
    return check_agent(p)
```

`register(applies)` appends `(applies, func)` to `CHECKS`; `run_all(p)` runs the applicable ones **in registration order** and merges their dictionaries. A new check only needs a registered function; the model does not change. Current order: HTTP, TCP, SSL (HTTP only), agent, domain.

Each function returns monitor field values. `ok` (bool) is consumed by `_apply_result` and turned into `status` (`up`/`down`). The wrapper functions look up the real function by name at call time because the tests patch them on the module.

Relevant details:

- **Odoo mode** (`p['odoo']`): forces `GET {origin}/web/login` with `stream=True`, measures `response.elapsed` (until headers), reads ≤ 64 KiB and validates markers (`/web/assets/`, `__session_info__`, `oe_login_form`, `o_database_list`, `odoo`).
- **Domain**: RDAP (`https://rdap.org/domain/<domain>`), once a day (`domain_checked_at`). `registered_domain()` applies the second-level suffix rule; `PLATFORM_DOMAINS` are skipped.
- **Agent**: `GET agent_url` with `Authorization: Bearer <token>`. A failure only sets `agent_status`; it leaves the last DB-validity values untouched.
- **DB validity** (`database_values`): a reported `expiration_date` wins; if there is none and `estimate_days > 0`, it estimates `create_date + estimate_days` and sets `db_expiry_estimated`.

## 4. Models

### `website.monitor` (inherits `mail.thread` + 4 mixins)

Own fields: configuration (`name`, `url`, `check_type`, `tcp_port`, `http_method`, `expected_status`, `expected_keyword`, `custom_headers`*, `auth_user`*, `auth_password`*, `verify_ssl`, `is_odoo`, `check_domain_expiry`, `check_interval`, `timeout`, `failure_threshold`, `max_response_time`, `next_check`, computed `color`). \* restricted to the manager group.

Constraints: interval, timeout and threshold > 0; valid TCP port on TCP monitors. `@api.onchange('is_odoo')` turns `agent_enabled` off when the monitor stops being Odoo.

Hooks it overrides: `_extra_problems()` (resource thresholds) and `_expiry_sources()` (adds the DB).

### Mixins (one responsibility each)

| Mixin | Responsibility | Key points |
|---|---|---|
| `website.monitor.result.mixin` | Last-result fields | `status`, `last_checked`, `response_time`, `http_*`, `ssl_*`, `domain_*`. |
| `website.monitor.incident.mixin` | Incidents and expiries | `_apply_result`, `_has_problem`, `_open_or_continue_incident`, `_close_incident`, `_check_expiry_alerts`, `parse_days`. |
| `website.monitor.notification.mixin` | Emails | `_notify(kind)` with `kind` ∈ `alert`, `recovery`, `ssl_expiry`, `domain_expiry`, `db_expiry`. |
| `website.monitor.resources.mixin` | Agent and resources | `_agent_params`, `_resource_problems`, `_db_expiry_sources`, `_db_expiring`, `_resource_history_vals`, `action_view_metric` (opens a metric's chart). |
| `website.monitor.stats.mixin` | History and uptime | `history_count`, `uptime_24h/7d/30d`. |

### Incident state machine

- `_has_problem()`: `status == 'down'`, an SSL error (`Valid`, `Expiring soon` and `Not HTTPS` are tolerated), a response slower than `max_response_time`, or `_extra_problems()`.
- With a problem: `consecutive_failures += 1`, sets `problem_since`; on reaching `failure_threshold` it sends **one** alert (`service_notified`).
- Without a problem: if an alert was sent, it computes the downtime minutes and sends a recovery; counters are reset.

### Expiry alerts

`_expiry_sources()` returns tuples `(date_field, level_field, notification_type, levels)`. For each one, `days = (date - now).days` and `level` = the smallest configured level that is ≥ days (0 if the date is still further away than the largest level). A notification is sent when there is a level and `previous` is empty or the new level is lower; the stored level (`ssl_alert_level`, `domain_alert_level`, `db_alert_level`) prevents repeats and is reset on renewal. Adding a new source = returning another tuple.

### History

`website.monitor.history`: one row per cron check or *Check Now* (`response_time`, `http_status`, `status`, `cpu_percent`, `memory_percent`, `disk_percent`, `requests_in_flight`; `avg`/`max` aggregates for charts and pivot). `@api.autovacuum` deletes rows older than `server_uptime_monitor.history_retention_days` (90).

### Groups and contacts

`website.monitor.groups` (Odoo users + contacts) and `website.monitor.contacts` (email, phone, notification type).

## 5. Monitor security

| Element | Detail |
|---|---|
| Groups | `group_website_monitor_user` (read) and `group_website_monitor_manager` (CRUD), under the *Website Monitor* category/privilege. |
| Access rights | `security/ir.access.csv` (20) · `ir.model.access.csv` (19, 18, 17). All 4 models have read for user and CRUD for manager. |
| Fields | `auth_password`, `agent_token`, `auth_user` and `custom_headers` (which often holds API keys) have `groups=manager`; they are read with `sudo()` in `_check_params`/`_agent_params`. |
| Public methods | RPC can call any public method as any user who can read the record. `action_send_mail` therefore calls `check_access('write')`; the cron entry point is private (`_cron_check_websites`). |
| Agent URL | An explicit *Agent URL* must be `https://` (loopback excepted) because the token travels in a header. When it is derived from an `http://` site URL, use HTTPS or a trusted network. |
| `/status` | `auth='public'`; only name, status and 30-day uptime; no URLs. |
| Cron / SSRF | Runs as the cron superuser; network calls originate from the monitoring server. Managers choose the URLs, hosts and TCP ports, so they can probe the internal network of that server (responses leak through status, error text and keyword checks) and redirects are followed. Grant *Administrator* only to trusted people. |

## 6. `server_resource_agent`

### Endpoint

`GET /server_agent/metrics` — `auth='public'`, `csrf=False`, `sitemap=False`, route kwarg `agent_probe=True`.

Order of the checks (any failure → **404**, so the endpoint is not advertised):

1. `server_resource_agent.allowed_ips` (optional; comma-separated IPs/CIDRs; empty = all). Behind a reverse proxy enable `proxy_mode`.
2. Token: `Authorization: Bearer <token>` or `X-Agent-Token`, compared with `hmac.compare_digest` against `server_resource_agent.token`. With no token configured, always 404.

Response (`Cache-Control: no-store`):

```json
{
  "timestamp": 1730000000.0,
  "cpu": {"percent": 12.3, "count": 8, "load_avg": [0.5, 0.4, 0.3]},
  "memory": {"total": 0, "available": 0, "percent": 41.0, "swap_percent": 0.0, "odoo_rss": 0},
  "disks": [{"name": "data_dir", "path": "/var/lib/odoo", "total": 0, "used": 0, "percent": 55.0}],
  "database": {"expiration_date": "2026-12-01 00:00:00", "expiration_reason": "trial", "create_date": "2026-11-01 10:00:00"},
  "requests": {"in_flight": 2, "peak": 5, "workers": 4, "db_active_queries": 1}
}
```

### Metrics

- **CPU**: percentage between two readings (`/proc` delta stored in `cpu_sample`; the sample expires after 3600 s).
- **Memory**: RSS of the Odoo process tree (the parent if there are workers) plus system memory.
- **Disks**: `data_dir` and `/`; one whose `(total, used)` matches another is skipped (the same disk mounted twice in Docker).
- **Database**: direct SQL on `ir_config_parameter` (`database.expiration_date`, `database.expiration_reason`, `database.create_date`).

### Concurrent requests

`ir.http._dispatch` is overridden to call `inflight.enter()/leave()` (except routes with `agent_probe`). With several workers an in-memory counter is useless, so **each process writes `current peak peak_start` to its own file** inside `$TMP/odoo_resource_agent_<uid>` (a `0700` directory; it is ignored unless it is a real directory owned by the Odoo user and closed to others, and files are opened with `O_NOFOLLOW`) and the endpoint sums those of live processes. The **peak** is reset when the endpoint reads it (`last_read` file), so a short spike between two polls is not lost. All writes are best-effort: they never break a request.

### Installation

`post_init_hook(env)` generates the token (`secrets.token_urlsafe(32)`) if it does not exist. To rotate it, change the system parameter and update the monitor.

## 7. System parameters

| Key | Module | Effect |
|---|---|---|
| `server_uptime_monitor.history_retention_days` | monitor | Days of history (90). |
| `server_resource_agent.token` | agent | Access token (generated on install). |
| `server_resource_agent.allowed_ips` | agent | Optional IP/CIDR allow-list. |

## 8. Tests

50 tests (`tests/test_website_monitor.py`, `tests/test_agent.py`) covering: HTTP/TCP/SSL/Odoo/domain checks, the check registry, incidents and recovery, expiry levels, day estimation and validation, resources and thresholds, the endpoint (token, IP, 404), the request counter, DB validity, and security (secret fields, public methods, untrusted temp directory, HTTPS agent URL). Network calls are patched in `monitor_checks`.

```
odoo -d test -i server_uptime_monitor,server_resource_agent \
     --test-tags /server_uptime_monitor,/server_resource_agent --stop-after-init
```

Note: after writing with the ORM and before reading with direct SQL, call `env.flush_all()`.

## 9. Differences per version

| Topic | 20.0 | 19.0 | 18.0 | 17.0 |
|---|---|---|---|---|
| Access rights | `ir.access.csv` | `ir.model.access.csv` | same as 19 | same as 19 |
| SQL constraints | `models.Constraint` | `models.Constraint` | `_sql_constraints` | `_sql_constraints` |
| Groups | `res.groups.privilege` + `user_ids` | same | `ir.module.category` + `users` | same as 18 |
| Parameters | `get_str/set_str/get_int` | `get_param/set_param` | same as 19 | same as 19 |
| Translation in Python | `self.env._()` | `self.env._()` | `self.env._()` | `from odoo import _` |
| Views | `<list>`, kanban `card` | same | `<list>`, kanban `card` | `<tree>`, `kanban-box`, chatter `div.oe_chatter` |
| Cron | no `numbercall` | same | same | `numbercall = -1` |
| Aggregatable fields | `aggregator` | `aggregator` | `aggregator` | `group_operator` |

How to port a fix: apply it on `20.0` and `git cherry-pick` it to `19.0`, `18.0` and `17.0`; resolve only the conflicts from the table above and run the tests with the official `odoo:<version>` image.

## 10. How to extend

- **New check type**: a function in `monitor_checks.py` with `@register(lambda p: ...)` that returns fields; if it needs settings, add fields to the model and expose them in `_check_params`.
- **New expiry source**: return another tuple in `_expiry_sources()` and add the level field and the template/type in `_TEMPLATES`.
- **New agent metric**: add the key in `metrics.collect`, the field in the resources mixin, `_resource_history_vals` and the entry in `METRICS`.
- **New channel (e.g. Telegram)**: implement it in `monitor_notification.py` according to the contact's `notification_type`.
