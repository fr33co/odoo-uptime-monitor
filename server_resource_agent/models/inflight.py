"""Cross-process counter of requests being served.

Odoo may run many worker processes, so a plain in-memory counter would only
see one of them. Each process writes ``<current> <peak> <peak_started>`` to its
own small file; the metrics endpoint sums the files of live processes. The
peak is reset whenever the endpoint reads it, so a short spike between two
polls is not missed.
"""
import os
import stat
import tempfile
import threading
import time

import psutil

DIR = os.path.join(tempfile.gettempdir(), 'odoo_resource_agent_%s' % os.getuid())
READ_MARK = os.path.join(DIR, 'last_read')

_lock = threading.Lock()
_state = {'current': 0, 'peak': 0, 'peak_started': 0.0}


def safe_dir():
    """Create the working directory and check it is really ours.

    The directory lives in the shared temp folder under a predictable name,
    so another local user could have planted it (or a symlink) beforehand:
    refuse it unless it is a real directory owned by this user and closed to
    everyone else.
    """
    try:
        os.makedirs(DIR, mode=0o700, exist_ok=True)
        info = os.lstat(DIR)
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and not info.st_mode & 0o077)


def write_file(path, text):
    """Write ``text`` to ``path`` without following a symlink planted there."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)


def _write():
    """Persist this process's counters to its own file (best effort)."""
    try:
        if safe_dir():
            write_file(os.path.join(DIR, str(os.getpid())), '%d %d %f' % (
                _state['current'], _state['peak'], _state['peak_started']))
    except OSError:
        pass  # monitoring must never break a request


def _reset_peak_if_read():
    """Restart the peak when the endpoint has read the counters since it started."""
    try:
        marked = os.lstat(READ_MARK).st_mtime
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
    if not safe_dir():
        return 0, 0
    try:
        last_read = os.lstat(READ_MARK).st_mtime
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
        write_file(READ_MARK, '')
        # Exact timestamp: the file system's own mtime is too coarse to be
        # compared reliably with the peak start times.
        now = time.time()
        os.utime(READ_MARK, (now, now), follow_symlinks=False)
    except OSError:
        pass
    return current, peak
