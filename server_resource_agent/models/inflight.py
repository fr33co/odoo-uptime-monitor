"""Cross-process counter of requests being served.

Odoo may run many worker processes, so a plain in-memory counter would only
see one of them. Each process writes ``<current> <peak> <peak_started>`` to its
own small file; the metrics endpoint sums the files of live processes. The
peak is reset whenever the endpoint reads it, so a short spike between two
polls is not missed.
"""
import os
import tempfile
import threading
import time

import psutil

DIR = os.path.join(tempfile.gettempdir(), 'odoo_resource_agent_%s' % os.getuid())
READ_MARK = os.path.join(DIR, 'last_read')

_lock = threading.Lock()
_state = {'current': 0, 'peak': 0, 'peak_started': 0.0}


def _write():
    """Persist this process's counters to its own file (best effort)."""
    try:
        os.makedirs(DIR, mode=0o700, exist_ok=True)
        with open(os.path.join(DIR, str(os.getpid())), 'w') as f:
            f.write('%d %d %f' % (
                _state['current'], _state['peak'], _state['peak_started']))
    except OSError:
        pass  # monitoring must never break a request


def _reset_peak_if_read():
    """Restart the peak when the endpoint has read the counters since it started."""
    try:
        marked = os.stat(READ_MARK).st_mtime
    except OSError:
        return
    if marked > _state['peak_started']:
        _state['peak'] = _state['current']
        _state['peak_started'] = time.time()


def enter():
    """A request starts."""
    with _lock:
        _reset_peak_if_read()
        if not _state['peak_started']:
            _state['peak_started'] = time.time()
        _state['current'] += 1
        _state['peak'] = max(_state['peak'], _state['current'])
        _write()


def leave():
    """A request ends."""
    with _lock:
        _state['current'] = max(0, _state['current'] - 1)
        _write()


def read():
    """Return ``(in_flight, peak)`` summed over live processes, then reset the peaks."""
    current = peak = 0
    try:
        last_read = os.stat(READ_MARK).st_mtime
    except OSError:
        last_read = 0.0
    try:
        names = os.listdir(DIR)
    except OSError:
        names = []
    for name in names:
        if not name.isdigit():
            continue
        path = os.path.join(DIR, name)
        if not psutil.pid_exists(int(name)):
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        try:
            with open(path) as f:
                cur, pk, started = f.read().split()
            current += int(cur)
            # A peak recorded before the previous read was already reported.
            peak += int(cur) if float(started) <= last_read else max(int(pk), int(cur))
        except (OSError, ValueError):
            continue
    try:
        os.makedirs(DIR, mode=0o700, exist_ok=True)
        with open(READ_MARK, 'w') as f:
            f.write('')
        # Exact timestamp: the file system's own mtime is too coarse to be
        # compared reliably with the peak start times.
        now = time.time()
        os.utime(READ_MARK, (now, now))
    except OSError:
        pass
    return current, peak
