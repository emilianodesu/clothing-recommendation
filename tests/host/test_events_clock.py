"""Catch incorrect attribution of clock gaps and leakage from power logs."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))


class EventsClockTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('events_clock'), 'Missing clock diagnostics')
        import events_clock
        return events_clock

    def test_clock_gap_is_retained_without_claiming_sleep(self):
        m = self.module()
        result = m.analyze_timing('2026-10-22T00:00:00Z', '2026-10-22T00:15:00Z', 1, [
            dict(phase='after_seed', service='ui', captured_at='2026-10-22T00:00:00Z', elapsed_seconds=0),
            dict(phase='after_seed', service='postgres', captured_at='2026-10-22T00:15:00Z', elapsed_seconds=1)])
        self.assertEqual(result['utc_elapsed_seconds'], 900)
        self.assertEqual(result['difference_seconds'], 899)
        self.assertEqual(result['intervals'][0]['difference_seconds'], 899)
        self.assertFalse(result['cause_confirmed'])

    def test_invalid_clock_values_and_naive_timestamps_are_rejected(self):
        m = self.module()
        for bad in [True, -1, float('nan'), float('inf')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                m.analyze_timing('2026-10-22T00:00:00Z', '2026-10-22T00:01:00Z', bad, [])
        with self.assertRaises(ValueError):
            m.analyze_timing('2026-10-22T00:00:00', '2026-10-22T00:01:00Z', 1, [])

    def test_power_log_keeps_only_window_categories_and_normalizes_offsets(self):
        m = self.module()
        log = ['2026-10-21 17:59:00 -0600 Assertions PRIVATE_USER',
               '2026-10-21 18:00:10 -0600 Sleep PRIVATE_REASON',
               '2026-10-21 18:00:30 -0600 DarkWake PRIVATE_DEVICE',
               '2026-10-21 18:00:40 -0600 Wake PRIVATE_APP',
               '2026-10-21 18:00:50 -0600 Unknown PRIVATE_PATH',
               '2026-10-21 18:02:00 -0600 Wake PRIVATE_OUTSIDE']
        value = m.parse_power(log, '2026-10-22T00:00:00Z', '2026-10-22T00:01:00Z')
        self.assertEqual(value['counts'], {'sleep': 1, 'wake': 1, 'darkwake': 1})
        self.assertEqual(value['events'][0], {'at': '2026-10-22T00:00:10+00:00', 'kind': 'sleep'})
        self.assertTrue(value['log_brackets_window'])
        self.assertEqual(value['unrecognized_window_rows'], 1)
        self.assertNotIn('PRIVATE', json.dumps(value))

    def test_empty_old_unknown_or_truncated_logs_never_assert_no_sleep(self):
        m = self.module()
        for lines in [[], ['2026-01-01 00:00:00 +0000 Sleep reason'], ['unknown layout']]:
            with self.subTest(lines=lines):
                v = m.parse_power(lines, '2026-10-22T00:00:00Z', '2026-10-22T00:01:00Z')
                self.assertFalse(v['log_brackets_window'])
        lines = ['2026-10-22 00:00:30 +0000 Sleep reason'] * 257
        v = m.parse_power(lines, '2026-10-22T00:00:00Z', '2026-10-22T00:01:00Z')
        self.assertEqual(v['counts']['sleep'], 257)
        self.assertEqual(len(v['events']), 256)
        self.assertTrue(v['events_truncated'])

    def test_process_capture_is_bounded_without_exporting_output(self):
        m = self.module()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'raw'
            state = m.capture_log([sys.executable, '-c', 'import sys;sys.stdout.write("x"*200000)'], path, max_bytes=4096)
            self.assertEqual(state, 'size_limit')
            self.assertLessEqual(path.stat().st_size, 4096)
            state = m.capture_log([sys.executable, '-c', 'import time;time.sleep(2)'], path, timeout=.05)
            self.assertEqual(state, 'timeout')
