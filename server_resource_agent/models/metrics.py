"""Collect host and Odoo figures with psutil. Cheap and non-blocking."""
import os
import time

import psutil

from odoo.tools import config

from . import inflight

CPU_SAMPLE = os.path.join(inflight.DIR, 'cpu_sample')
MAX_SAMPLE_AGE = 3600


def _busy_total():
    """Return ``(busy, total)`` CPU seconds since boot."""
    t = psutil.cpu_times()
    total = sum(t)
    idle = t.idle + getattr(t, 'iowait', 0.0)
    return total - idle, total


def cpu_percent():
    """CPU use since the previous read, shared by all processes via a file.

    Falls back to a short 0.3 s sample when there is no recent previous one.
    """
    busy, total = _busy_total()
    now = time.time()
    previous = None
    try:
        with open(CPU_SAMPLE) as f:
            ts, pbusy, ptotal = (float(x) for x in f.read().split())
        if now - ts < MAX_SAMPLE_AGE and total > ptotal:
            previous = (pbusy, ptotal)
    except (OSError, ValueError):
        pass
    if previous is None:
        return psutil.cpu_percent(interval=0.3), busy, total, now
    pct = 100.0 * (busy - previous[0]) / (total - previous[1])
    return max(0.0, min(100.0, pct)), busy, total, now


def _save_cpu_sample(busy, total, now):
    """Store the CPU sample used as baseline by the next read (atomic, best effort)."""
    try:
        os.makedirs(inflight.DIR, mode=0o700, exist_ok=True)
        tmp = CPU_SAMPLE + '.%d' % os.getpid()
        with open(tmp, 'w') as f:
            f.write('%f %f %f' % (now, busy, total))
        os.replace(tmp, CPU_SAMPLE)
    except OSError:
        pass


def odoo_memory():
    """RSS in bytes of the whole Odoo process tree (main process plus workers)."""
    try:
        process = psutil.Process(os.getpid())
        if config['workers'] and process.parent():
            process = process.parent()
        procs = [process] + process.children(recursive=True)
        return sum(p.memory_info().rss for p in procs if p.is_running())
    except psutil.Error:
        return None


def disks():
    """Usage of the Odoo data directory and of the root filesystem.

    A path whose usage figures equal one already listed is skipped: in Docker
    the data directory is often a different mount of the same disk.
    """
    result, seen = [], set()
    for label, path in (('data_dir', config['data_dir']), ('root', '/')):
        try:
            usage = psutil.disk_usage(path)
        except OSError:
            continue
        key = (usage.total, usage.used)
        if key in seen:
            continue
        seen.add(key)
        result.append({'name': label, 'path': path, 'total': usage.total,
                       'used': usage.used, 'percent': usage.percent})
    return result


def database_validity(cr):
    """Expiration of the database as recorded by Odoo (demos, trials, subscriptions).

    Returns ``expiration_date`` (UTC, ``YYYY-MM-DD HH:MM:SS``) and
    ``expiration_reason`` (``trial``, ``demo``, ...), both ``None`` on
    databases without that information (e.g. Community), plus ``create_date``
    (when the database was created), which the monitor can use to estimate a
    validity.
    """
    cr.execute("""
        SELECT key, value FROM ir_config_parameter
         WHERE key IN ('database.expiration_date', 'database.expiration_reason',
                       'database.create_date')""")
    values = dict(cr.fetchall())
    return {
        'expiration_date': values.get('database.expiration_date') or None,
        'expiration_reason': values.get('database.expiration_reason') or None,
        'create_date': values.get('database.create_date') or None,
    }


def collect(cr):
    """Return the metrics dict served by the endpoint."""
    cpu, busy, total, now = cpu_percent()
    _save_cpu_sample(busy, total, now)
    in_flight, peak = inflight.read()
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    try:
        cr.execute("""
            SELECT count(*) FROM pg_stat_activity
             WHERE datname = current_database() AND state = 'active'
               AND pid <> pg_backend_pid()""")
        db_active = cr.fetchone()[0]
    except Exception:  # noqa: BLE001 - optional figure
        db_active = None
    return {
        'timestamp': now,
        'cpu': {
            'percent': round(cpu, 1),
            'count': psutil.cpu_count(),
            'load_avg': [round(x, 2) for x in os.getloadavg()],
        },
        'memory': {
            'total': mem.total, 'available': mem.available,
            'percent': mem.percent, 'swap_percent': swap.percent,
            'odoo_rss': odoo_memory(),
        },
        'disks': disks(),
        'database': database_validity(cr),
        'requests': {
            'in_flight': in_flight,
            'peak': peak,
            'workers': config['workers'],
            'db_active_queries': db_active,
        },
    }
