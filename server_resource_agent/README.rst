=====================
Server Resource Agent
=====================

Install this module on every Odoo server that the *Server Uptime Monitor*
should watch. It publishes a read-only JSON endpoint with the server
resources and the validity of the Odoo database.

Not available on Odoo Online / Odoo.sh: modules that read the host cannot be
relied on there.

Requirements
============

* Python package ``psutil``.

Configuration
=============

The token is generated on install and stored in the system parameter
``server_resource_agent.token`` (*Settings > Technical > System Parameters*).
Paste it into the *Agent Token* field of the monitor.

Optional system parameter ``server_resource_agent.allowed_ips``:
comma-separated IPs or CIDRs allowed to call the endpoint (empty = any).
Behind a reverse proxy enable ``proxy_mode`` so the real client IP is seen.

Endpoint
========

``GET /server_agent/metrics`` with ``Authorization: Bearer <token>`` (or the
header ``X-Agent-Token``). Any other caller, a wrong token or a disallowed IP
gets a plain 404, so the endpoint is not advertised.

Returns CPU (percent, count, load average), memory (system and Odoo RSS),
disks (data directory and root, de-duplicated), requests in flight and their
peak since the last read, and the database validity (``expiration_date``,
``expiration_reason``, ``create_date``).

Concurrent requests are counted across worker processes with one small file
per process under the temporary directory; the agent's own route is excluded.

Usage example
=============

::

   curl -H "Authorization: Bearer $TOKEN" https://host/server_agent/metrics

See ``docs/tecnica.md`` in the repository for the response format and design.

Tests
=====

``odoo -d <db> -i server_resource_agent --test-tags /server_resource_agent``
