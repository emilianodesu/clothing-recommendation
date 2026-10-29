"""Catch false acceptance after suspension, clock changes and guard leaks."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))


class ClockGuardTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('clock_guard'), 'Missing temporal guard')
        import clock_guard
        return clock_guard

    def timeline(self, points):
        m = self.module()
        samples = iter(points)
        return m.Timeline(sample=lambda: next(samples))

    def test_coherent_elapsed_is_not_cpu_time_and_budget_is_preserved(self):
        t = self.timeline([(100., 1.), (110., 11.)])
        t.mark('start'); t.mark('end')
        self.assertEqual(t.check()['remaining_seconds'], 7190)
        self.assertEqual(len(t.samples), 2)

    def test_suspend_clock_jump_and_budget_exhaustion_cannot_pass(self):
        for points in [[(100., 1.), (1000., 2.)], [(100., 1.), (99., 2.)],
                       [(100., 2.), (101., 1.)], [(100., 1.), (7301., 7202.)],
                       [(100., 1.), (101., float('nan'))]]:
            with self.subTest(points=points):
                t = self.timeline(points); t.mark('start')
                with self.assertRaises(ValueError):t.mark('end')

    def test_cumulative_drift_cannot_hide_in_small_intervals(self):
        t = self.timeline([(0., 0.), (2., 1.), (4., 2.), (6., 3.)])
        t.mark('a');t.mark('b');t.mark('c')
        with self.assertRaises(ValueError):t.mark('d')

    def test_awake_guard_reaps_only_owned_process_on_failure(self):
        m = self.module()
        import subprocess
        real_popen = subprocess.Popen
        children = []
        def spawn(command, **kwargs):
            self.assertEqual(command[:3], ['/usr/bin/caffeinate', '-is', '-w'])
            p = real_popen([sys.executable, '-c', 'import time;time.sleep(60)'], **kwargs)
            children.append(p)
            return p
        with patch.object(m.platform, 'system', return_value='Darwin'), patch.object(m.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaises(RuntimeError):
                with m.AwakeGuard():raise RuntimeError('controlled failure')
        self.assertIsNotNone(children[0].returncode)

    def test_unsupported_or_dead_awake_guard_is_explicit(self):
        m = self.module()
        with patch.object(m.platform, 'system', return_value='Linux'), self.assertRaises(ValueError):
            with m.AwakeGuard():pass
