"""Regressions for attributing cgroup pressure without erasing lifetime evidence."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))


def snapshot(phase='before_seed', service='postgres', elapsed=1):
    return dict(phase=phase, service=service, elapsed_seconds=elapsed,
        captured_at='2026-10-22T00:00:00+00:00', sample_seconds=0.1,
        container_id='a'*64, started_at='2026-10-21T00:00:00Z', restart_count=0,
        cgroup_inode=1234, image_id='sha256:'+'b'*64,
        memory_bytes=2**29, memory_swap_bytes=2**30, nano_cpus=0, oom_killed=False,
        current_bytes=400000000, peak_bytes=537141248, swap_current_bytes=0, swap_peak_bytes=0,
        cgroup_limit_bytes=2**29, cgroup_swap_limit_bytes=2**29,
        events=dict(low=0, high=0, max=45963, oom=0, oom_kill=0, oom_group_kill=0),
        stat=dict(anon=100000000, file=280000000, kernel=20000000, shmem=10000000),
        pressure_us=dict(some=10000, full=1000))


class EventsMemoryTests(unittest.TestCase):
    def module(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('events_memory'), 'Missing phase diagnostic implementation')
        import events_memory
        return events_memory

    def test_historical_peak_is_preserved_without_claiming_interval_growth(self):
        m=self.module();a=snapshot();b=snapshot('after_seed',elapsed=5)
        d=m.interval(a,b)
        self.assertTrue(d['comparable'])
        self.assertEqual(d['events_delta']['max'],0)
        self.assertEqual(d['peak_growth_bytes'],0)
        self.assertEqual(d['pressure_delta_us'],{'some':0,'full':0})
        self.assertTrue(d['boundary_reached_before'])
        self.assertEqual(a['peak_bytes'],537141248)

    def test_new_pressure_oom_and_swap_are_separate_signals(self):
        m=self.module();a=snapshot();b=snapshot('after_seed',elapsed=5)
        b['events'].update(max=45966,oom=1,oom_kill=1)
        b['pressure_us'].update(some=10200,full=1050)
        b['swap_peak_bytes']=4096
        d=m.interval(a,b)
        self.assertEqual(d['events_delta']['max'],3)
        self.assertEqual(d['events_delta']['oom_kill'],1)
        self.assertEqual(d['pressure_delta_us'],{'some':200,'full':50})
        self.assertEqual(d['swap_peak_growth_bytes'],4096)

    def test_generation_change_never_subtracts_reset_counters(self):
        m=self.module();a=snapshot();b=snapshot('after_restart',elapsed=5)
        b.update(started_at='2026-10-22T00:00:01Z',cgroup_inode=5678,peak_bytes=1000000)
        b['events']['max']=0
        d=m.interval(a,b)
        self.assertFalse(d['comparable'])
        self.assertEqual(d['reason'],'generation_changed')
        self.assertIsNone(d['events_delta']);self.assertIsNone(d['peak_growth_bytes'])

    def test_counter_decrease_with_stable_identity_is_invalid(self):
        m=self.module();a=snapshot();b=snapshot('after_seed',elapsed=5)
        b['events']['max']-=1
        with self.assertRaises(ValueError):m.interval(a,b)

    def test_missing_metrics_and_unbounded_fields_are_rejected(self):
        m=self.module()
        for edit in [lambda s:s['events'].pop('oom'),lambda s:s.update(current_bytes=True),
                     lambda s:s.update(extra='untrusted'),lambda s:s.update(peak_bytes=-1),
                     lambda s:s['pressure_us'].update(some=float('nan'))]:
            s=snapshot();edit(s)
            with self.subTest(s=s),self.assertRaises(ValueError):m.validate_snapshot(s)

    def test_different_service_or_nonincreasing_time_is_invalid(self):
        m=self.module();a=snapshot()
        for b in [snapshot('after_seed',service='api',elapsed=5),snapshot('after_seed',elapsed=1)]:
            with self.assertRaises(ValueError):m.interval(a,b)
