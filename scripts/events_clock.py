"""Read-only, bounded clock/power evidence. Never export raw power messages."""
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import platform
import re
import selectors
import subprocess
import tempfile
import time

MAX_EVENTS = 256
POWER_ROW = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s*([+-]\d{4})\s+(\S+)')


def require(value):
    if not value:
        raise ValueError('Invalid clock evidence')


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def utc(value):
    require(isinstance(value, str) and len(value) <= 40)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None)
    return parsed.astimezone(timezone.utc)


def analyze_timing(started_at, finished_at, duration, snapshots):
    start, end = utc(started_at), utc(finished_at)
    require(end >= start and number(duration))
    require(isinstance(snapshots, list) and len(snapshots) <= 12)
    reduced = []
    for s in snapshots:
        require(s['phase'] in ('before_seed', 'after_seed', 'after_replay', 'after_restart'))
        require(s['service'] in ('api', 'ui', 'postgres') and number(s['elapsed_seconds']))
        at = utc(s['captured_at'])
        require(start <= at <= end and s['elapsed_seconds'] <= duration)
        reduced.append({k: s[k] for k in ('phase', 'service', 'captured_at', 'elapsed_seconds')})
    intervals = []
    for a, b in zip(reduced, reduced[1:]):
        elapsed = b['elapsed_seconds'] - a['elapsed_seconds']
        require(elapsed > 0)
        wall = (utc(b['captured_at']) - utc(a['captured_at'])).total_seconds()
        intervals.append(dict(from_phase=a['phase'], from_service=a['service'],
            to_phase=b['phase'], to_service=b['service'], utc_seconds=wall,
            monotonic_seconds=elapsed, difference_seconds=wall-elapsed))
    wall = (end-start).total_seconds()
    return dict(started_at=started_at, finished_at=finished_at, monotonic_duration_seconds=duration,
        utc_elapsed_seconds=wall, difference_seconds=wall-duration, snapshots=reduced,
        intervals=intervals, cause_confirmed=False)


def parse_power(lines, start, end):
    start, end = utc(start), utc(end)
    require(start <= end)
    before = after = False
    unknown = malformed = rows = 0
    events = []
    counts = dict(sleep=0, wake=0, darkwake=0)
    for line in lines:
        match = POWER_ROW.match(line)
        if not match:
            if line[:4].isdigit():
                malformed += 1
            continue
        try:
            at = datetime.strptime(match[1]+match[2], '%Y-%m-%d %H:%M:%S%z').astimezone(timezone.utc)
        except ValueError:
            malformed += 1
            continue
        before = before or at <= start
        after = after or at >= end
        if not start <= at <= end:
            continue
        rows += 1
        kind = match[3].lower()
        if kind not in counts:
            unknown += 1
            continue
        counts[kind] += 1
        if len(events) < MAX_EVENTS:
            events.append(dict(at=at.isoformat(), kind=kind))
    events.sort(key=lambda e: (e['at'], e['kind']))
    return dict(log_brackets_window=before and after, window_timestamped_rows=rows,
        unrecognized_window_rows=unknown, malformed_timestamp_rows=malformed,
        counts=counts, events=events, events_truncated=sum(counts.values()) > MAX_EVENTS)


def capture_log(command, path, *, max_bytes=16*2**20, timeout=30):
    """Capture locally with byte/time bounds; return a category, never stderr."""
    begin, wall = time.monotonic(), time.time()
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    process = None
    try:
        with os.fdopen(fd, 'wb') as output:
            try:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except OSError:
                return 'command_unavailable'
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                count = 0
                while True:
                    if max(time.monotonic()-begin, time.time()-wall) >= timeout:
                        return 'timeout'
                    if not selector.select(min(.1, timeout)):
                        continue
                    data = os.read(process.stdout.fileno(), 65536)
                    if not data:
                        return 'complete' if process.wait(timeout=1) == 0 else 'command_failed'
                    output.write(data[:max(0, max_bytes-count)])
                    count += len(data)
                    if count > max_bytes:
                        return 'size_limit'
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def collect_power(start, end):
    result = parse_power([], start, end)
    if platform.system() != 'Darwin':
        return dict(result, collection_status='unsupported_platform')
    with tempfile.TemporaryDirectory(prefix='hmrec-power-') as directory:
        path = Path(directory) / 'pmset.log'
        try:
            status = capture_log(['/usr/bin/pmset', '-g', 'log'], path)
            with path.open(errors='replace') as stream:
                result = parse_power(stream, start, end)
        except (OSError, subprocess.SubprocessError):
            status = 'read_error'
        # Raw messages never enter a handoff folder, and are removed on exit.
        return dict(result, collection_status=status)


def clock_metadata():
    clocks = {}
    for name in ('time', 'monotonic', 'perf_counter'):
        info = time.get_clock_info(name)
        clocks[name] = dict(implementation=info.implementation, monotonic=info.monotonic,
                            adjustable=info.adjustable, resolution=info.resolution)
    return dict(system=platform.system(), system_version=platform.mac_ver()[0] if platform.system() == 'Darwin' else platform.release(),
                machine=platform.machine(), python=platform.python_version(), clocks=clocks)
