"""Temporal controls for a future runner; legacy T5 protocols are unchanged."""
import os
import platform
import re
import subprocess
import time
from events_clock import number, require


class Timeline:
    def __init__(self, sample=None):
        self.sample = sample or (lambda: (time.time(), time.monotonic()))
        self.samples = []

    def mark(self, label):
        require(isinstance(label, str) and re.fullmatch('[a-z][a-z0-9_-]{0,63}', label))
        wall, mono = self.sample()
        require(number(wall) and number(mono))
        self.samples.append(dict(label=label, utc_seconds=wall, monotonic_seconds=mono))
        return self.check()

    def check(self):
        require(bool(self.samples))
        start, end = self.samples[0], self.samples[-1]
        for a, b in zip(self.samples, self.samples[1:]):
            wall = b['utc_seconds']-a['utc_seconds']
            mono = b['monotonic_seconds']-a['monotonic_seconds']
            require(wall >= 0 and mono >= 0 and abs(wall-mono) <= 2)
        wall = end['utc_seconds']-start['utc_seconds']
        mono = end['monotonic_seconds']-start['monotonic_seconds']
        require(abs(wall-mono) <= 2 and max(wall, mono) <= 7200)
        return dict(utc_elapsed_seconds=wall, monotonic_elapsed_seconds=mono,
                    remaining_seconds=7200-max(wall, mono))


class AwakeGuard:
    """Own one temporary caffeinate process; cannot prevent forced/lid sleep."""
    def __init__(self):
        self.process = None

    def __enter__(self):
        require(platform.system() == 'Darwin' and self.process is None)
        self.process = subprocess.Popen(['/usr/bin/caffeinate', '-is', '-w', str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(.05)
            self.check()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def check(self):
        require(self.process is not None and self.process.poll() is None)

    def __exit__(self, *_):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None

