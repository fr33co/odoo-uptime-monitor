# Server monitoring modules for Odoo

Two addons, one branch per Odoo version (`20.0`, `19.0`, `18.0`, `17.0`):

| Module | Install on | What it does |
|---|---|---|
| `server_uptime_monitor` | the Odoo that does the monitoring | Checks websites and TCP services (availability, response time, SSL, domain expiry), Odoo-aware slowness probe, server resources, Odoo database validity, email alerts, status page. |
| `server_resource_agent` | each Odoo server you want to monitor | Token-protected read-only endpoint `/server_agent/metrics` with CPU, memory, storage, concurrent requests and database validity. |

Each module has its own README / manifest description. The branch name is the
Odoo version the code targets; fixes are applied on `20.0` first and then
ported down.

Run the tests of a branch:

```
odoo -d <db> -i server_uptime_monitor,server_resource_agent \
     --test-tags /server_uptime_monitor,/server_resource_agent --stop-after-init
```
