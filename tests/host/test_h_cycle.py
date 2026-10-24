import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import h_cycle
from h_cycle import clock_bounds, cycle_metrics, decision, public_trace


class CycleMeasurement(unittest.TestCase):
    def test_resource_collection_cost_does_not_shift_ten_second_cadence(self):
        self.assertTrue(hasattr(h_cycle,'collect_periodically'))
        now=[0.];delays=[];samples=[]
        class Done:
            def wait(self,delay):
                delays.append(delay);now[0]+=delay
                return len(delays)==4
        def collect():samples.append(now[0]);now[0]+=3
        h_cycle.collect_periodically(Done(),collect,lambda:now[0])
        self.assertEqual(samples,[10.,20.,30.]);self.assertEqual(delays,[10.,7.,7.,7.])

    def clocks(self):
        return ([dict(before=100.,vm=10.,after=100.2)],
                [dict(before=200.,vm=110.,after=200.2)])

    def test_maps_vm_clock_to_conservative_host_window(self):
        bounds=clock_bounds(*self.clocks())
        self.assertAlmostEqual(bounds['low'],89.9)
        self.assertAlmostEqual(bounds['high'],90.3)
        trace=dict(started=100.,scheduled=200,rate=20,samples=[])
        marks=dict(start=11.,end=15.,wall_start=1000.,wall_end=1004.)
        result=cycle_metrics(trace,[dict(group='history')],marks,bounds)
        self.assertAlmostEqual(result['definite_start'],101.3)
        self.assertAlmostEqual(result['definite_end'],104.9)
        self.assertEqual(result['definite']['scheduled'],72)
        self.assertEqual(result['definite']['omitted'],72)
        self.assertEqual(result['definite']['error_fraction'],1.)

    def test_clock_drift_and_too_wide_brackets_rejected(self):
        for a,b in [([dict(before=100.,vm=10.,after=102.)],self.clocks()[1]),
                    (self.clocks()[0],[dict(before=201.,vm=110.,after=201.2)])]:
            with self.assertRaises(ValueError):clock_bounds(a,b)

    def test_sleep_or_wall_jump_invalidates_overlap(self):
        with self.assertRaises(ValueError):
            cycle_metrics({},[],dict(start=10.,end=14.,wall_start=100.,wall_end=110.),clock_bounds(*self.clocks()))

    def test_empty_interval_is_inconclusive_not_negative_duration(self):
        result=cycle_metrics(dict(started=100.,scheduled=200,rate=20,samples=[]),
            [dict(group='history')],dict(start=11.,end=11.1,wall_start=100.,wall_end=100.1),clock_bounds(*self.clocks()))
        self.assertIsNone(result['definite'])

    def good(self):
        return dict(error_type=None,resources_ok=True,source_preserved=True,auxiliaries_restored=True,
            worker_ok=True,measurement=dict(error_fraction=0.,latency_seconds={'p95':.01}),
            cycle=dict(definite=dict(scheduled=72,error_fraction=0.,latency_seconds={'p95':.01})))

    def test_short_real_cycle_can_pass_but_twenty_arrivals_required(self):
        report=self.good();self.assertEqual(decision(report),'passed')
        report['cycle']['definite']['scheduled']=19
        self.assertEqual(decision(report),'inconclusive')
        report['source_preserved']=False
        self.assertEqual(decision(report),'failed')

    def test_omission_or_slow_request_cannot_be_hidden_by_small_sample(self):
        for metric in ('error_fraction','latency_seconds'):
            report=self.good()
            report['cycle']['definite'][metric]=.02 if metric=='error_fraction' else {'p95':.2}
            self.assertEqual(decision(report),'failed')

    def test_public_trace_excludes_payloads_and_absolute_monotonic_origin(self):
        raw=dict(started=100.,scheduled=1,rate=20,samples=[dict(group='history',due=100.1,
            finished=100.2,latency=.1,service=.09,ok=True,customer_id='private')])
        exported=public_trace(raw)
        self.assertNotIn('started',exported)
        self.assertEqual(set(exported['samples'][0]),{'group','due','finished','latency','service','ok'})
        self.assertAlmostEqual(exported['samples'][0]['due'],.1)
