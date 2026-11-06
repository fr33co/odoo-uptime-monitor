====================
Server Uptime Monitor
====================

Monitors the availability, response time and SSL certificate of websites and
TCP services, and alerts the people you choose by email.

Configuration
=============

#. Open *Website Monitor > Configuration > Groups* and create a notification
   group. Add Odoo users (they are mailed at the email of their contact card)
   and/or external contacts (email only).
#. Open *Website Monitor > Monitored Websites* and create a monitor: name,
   URL (or host for TCP checks) and the notification group.
#. Make sure an outgoing mail server is configured in Odoo, otherwise alerts
   stay in the mail queue with an error.

Security groups (*Settings > Users*, section *Website Monitor*):

* **User**: read-only access to monitors, groups, contacts and history.
* **Administrator**: full access, including the Configuration menu.

How it works
============

A scheduled action runs every minute and checks only the monitors that are
due (each monitor has its own *Check Every (min)* and *Timeout (s)*). Network
checks run in parallel; results are stored on the monitor and one row is added
to the history.

Check options (tab *Check Settings*): HTTP method, expected status, expected
keyword, custom headers, basic authentication, SSL verification, TCP port
checks and domain expiry (via RDAP, once a day).

Alerts
======

* **Failure alert**: sent once per incident, after *Failures Before Alert*
  consecutive problems (default 2). A problem is a down site, an unexpected
  status, a missing keyword, an SSL error or a response slower than
  *Slow Threshold (ms)*.
* **Recovery notice**: sent when the monitor is healthy again, with the downtime
  in minutes.
* **Expiry warnings**: for the SSL certificate, the domain and the Odoo
  database, once at each configured day level before expiry (and when expired).
  Per monitor, *Warn SSL / Domain Expiry (days)* defaults to ``30,15,7,1`` and
  *Warn Database Expiry (days)* to ``5`` (add more levels, e.g. ``30,15,7,5,1``).

Odoo servers
============

Tick *Odoo Instance* on a monitor to measure responsiveness like an Odoo.sh
instance needs: one anonymous ``GET /web/login`` of the site origin (the
page needs a worker and the database), timing only the response headers and
reading at most 64 KiB to check that the answer really is Odoo. Set *Slow
Threshold (ms)* and *Failures Before Alert* to be warned only when the
instance stays slow.

Server resources (Odoo servers only)
------------------------------------

On servers you control, install the ``server_resource_agent`` module and
enable *Server Resources* on the monitor (agent URL optional, token from the
system parameter ``server_resource_agent.token``). Each check stores CPU,
memory, storage and the peak of concurrent requests, shows them on the
monitor with one line chart per metric over time, and alerts when a limit is
exceeded. An unreachable agent is shown in *Agent Status* but is not an
incident. Not available on Odoo Online / Odoo.sh.

Database validity (demos and trials)
------------------------------------

The agent also reports the expiration date Odoo keeps for the database
(system parameters ``database.expiration_date`` and
``database.expiration_reason``, filled by Odoo when it syncs with its
publisher). The monitor shows it in the *Status* tab and emails the group
(by default 5 days before; configurable per monitor), so a
demo or trial no longer dies unnoticed. The date can be stale until Odoo
syncs again, and it cannot be read on Odoo Online / Odoo.sh without the agent.

When the database reports **no** expiration date, the monitor estimates one as
the creation date (``database.create_date``) plus *Estimate Expiry After
(days)* (30 by default), flags it as *Estimated Expiry* on screen and in the
email, and alerts the same way. A reported date always wins. Set the days to 0
to disable the estimate, e.g. for databases that never expire (the "one month"
shown in Odoo's Settings page is only a placeholder, today plus 30 days).

Reporting
=========

* Kanban grouped by status, colored green / yellow / red, with 24h and 30d
  uptime.
* *Reporting > History*: line graph of the response time with one point per
  check (last 24 hours by default), plus list and pivot views.
* History older than 90 days is purged automatically. Change it with the
  system parameter ``server_uptime_monitor.history_retention_days``.

Public status page
==================

Monitors flagged *Show on Status Page* are listed at ``/status`` (name, status
and 30-day uptime only; URLs are never exposed).

Known limitations
=================

* Telegram contacts can be stored but no message is sent yet.
* The domain expiry check recognises common second-level suffixes
  (``.com.co``, ``.co.uk``, ``.gob.mx``...) with a simple rule, not a full
  public suffix list. Domains of the platform (``odoo.com``, ``odoo.sh``) are
  skipped on purpose.
* ``check_label`` (graph axis) is in UTC.
* Correct delivery depends on the outgoing mail server of the database.

Code structure
==============

* ``models/monitor_checks.py``: network checks, free of the ORM (thread-safe).
  Each check is registered with ``@register(applies)``; ``run_all()`` runs the
  applicable ones. To add a new kind of check, register a function there; the
  model does not change.
* ``models/website_monitor.py``: configuration and orchestration (cron,
  thread pool, check parameters).
* Mixins, one responsibility each: ``monitor_result`` (last result),
  ``monitor_incident`` (incident state, alert once, recovery, expiry alerts),
  ``monitor_notification`` (emails), ``monitor_resources`` (agent figures and
  thresholds) and ``monitor_stats`` (history and uptime).

Tests
=====

``odoo -d <db> -i server_uptime_monitor --test-tags /server_uptime_monitor``
